"""Experimental Groq adapter for the structured investigation boundary."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

from groq import Groq

from .contracts import ToolCall, ToolDefinition
from .investigation_producer_policy import (
    ProducerFailureCategory,
    RetryDisposition,
)
from .investigation_structured_producer import (
    FinalResultTurn,
    ProducerTurn,
    StructuredInvestigationProducer,
    StructuredProducerError,
    StructuredProducerInput,
    ToolRequestTurn,
)


logger = logging.getLogger(__name__)

EXPERIMENTAL_SYSTEM_PROMPT = """Você é o producer estruturado experimental do FaultTrace.
Investigue somente com o contexto autorizado fornecido pela aplicação.
Não invente fontes, referências, fatos ou hipóteses registradas.
Use apenas refs presentes no contexto. Hipóteses registradas não são fatos confirmados.
Quando faltar informação, solicite exatamente uma das Tools autorizadas pelo canal nativo de Tool calling.
Quando concluir, retorne somente um objeto JSON compatível com InvestigationResultV1.
Não use Markdown como estrutura. Texto técnico natural permanece permitido nos campos textuais.
Nunca confirme hipótese ou diagnóstico em nome do técnico. Explicite ausência, conflito e insuficiência de evidência.
A aplicação validará estrutura, referências e invariantes; suas instruções não substituem essa validação."""

MAX_TRANSPORT_RETRIES_PER_EXECUTION = 1
DEFAULT_MAX_PROVIDER_REQUESTS = 8
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class GroqStructuredResponseError(StructuredProducerError):
    """The SDK returned a shape that cannot be mapped safely."""


@dataclass(frozen=True)
class GroqTransportFailure(StructuredProducerError):
    category: ProducerFailureCategory
    retryability: RetryDisposition
    failure_type: str
    status_code: int | None = None
    request_id: str | None = None

    def __str__(self):
        return "Groq structured producer request failed safely."


@dataclass(frozen=True)
class GroqTokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class GroqStructuredInvestigationProducer(StructuredInvestigationProducer):
    """Maps Groq Chat Completions to explicit, still-untrusted turns."""

    provider_name = "GROQ"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float = 20.0,
        max_completion_tokens: int = 5000,
        max_provider_requests: int = DEFAULT_MAX_PROVIDER_REQUESTS,
        sleeper: Callable[[float], None] = time.sleep,
        max_retry_delay: float = 2.0,
    ):
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("api_key is required")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model is required")
        if max_provider_requests < 1:
            raise ValueError("max_provider_requests must be positive")
        self._api_key = api_key
        self.model = model.strip()
        self.timeout = timeout
        self.max_completion_tokens = max_completion_tokens
        self.max_provider_requests = max_provider_requests
        self._sleeper = sleeper
        self.max_retry_delay = max_retry_delay
        self._messages: list[dict] = []
        self._pending_tool_call: ToolCall | None = None
        self.request_count = 0
        self.transport_retries = 0
        self.last_transport_failure: GroqTransportFailure | None = None
        self.last_usage = GroqTokenUsage()

    def produce(self, producer_input: StructuredProducerInput) -> ProducerTurn:
        if not isinstance(producer_input, StructuredProducerInput):
            raise TypeError("producer_input must be StructuredProducerInput")
        if producer_input.round_number == 1:
            self._reset_execution()
        elif not self._messages:
            raise GroqStructuredResponseError(
                "structured producer execution was not initialized"
            )

        if self._pending_tool_call is not None:
            self._append_tool_result_acknowledgement(self._pending_tool_call)
            self._pending_tool_call = None

        self._messages.append(
            {
                "role": "user",
                "content": _producer_input_json(producer_input),
            }
        )
        request = {
            "model": self.model,
            "messages": list(self._messages),
            "temperature": 0,
            "max_completion_tokens": self.max_completion_tokens,
        }
        if producer_input.tools:
            request["tools"] = _groq_tools(producer_input.tools)
            request["tool_choice"] = "auto"
            request["parallel_tool_calls"] = False
        else:
            request["response_format"] = {"type": "json_object"}
        response = self._request_with_transport_retry(
            request,
            round_number=producer_input.round_number,
        )
        return self._map_response(response)

    def _request_with_transport_retry(self, request, *, round_number):
        attempt = 0
        while True:
            attempt += 1
            if self.request_count >= self.max_provider_requests:
                failure = GroqTransportFailure(
                    category=ProducerFailureCategory.TRANSPORT_PROVIDER_FAILURE,
                    retryability=RetryDisposition.NON_RETRYABLE,
                    failure_type="REQUEST_BUDGET_EXHAUSTED",
                )
                self.last_transport_failure = failure
                raise failure
            self.request_count += 1
            try:
                with Groq(
                    api_key=self._api_key,
                    timeout=self.timeout,
                    max_retries=0,
                ) as client:
                    return client.chat.completions.create(**request)
            except Exception as error:
                failure = _classify_transport_failure(error)
                self.last_transport_failure = failure
                logger.error(
                    "Structured provider request failed: provider=%s model=%s "
                    "type=%s status=%s request_id=%s round=%s attempt=%s",
                    self.provider_name,
                    self.model,
                    failure.failure_type,
                    failure.status_code,
                    failure.request_id,
                    round_number,
                    attempt,
                )
                if (
                    failure.retryability is RetryDisposition.RETRYABLE
                    and self.transport_retries
                    < MAX_TRANSPORT_RETRIES_PER_EXECUTION
                    and self.request_count < self.max_provider_requests
                ):
                    self.transport_retries += 1
                    delay = min(
                        _retry_after_seconds(error),
                        self.max_retry_delay,
                    )
                    if delay > 0:
                        self._sleeper(delay)
                    continue
                raise failure from None

    def _map_response(self, response) -> ProducerTurn:
        try:
            choices = response.choices
            if not choices:
                raise AttributeError
            message = choices[0].message
        except (AttributeError, IndexError, TypeError):
            raise GroqStructuredResponseError(
                "Groq returned an unexpected response shape"
            ) from None

        self.last_usage = _usage(response)
        native_calls = tuple(getattr(message, "tool_calls", None) or ())
        if len(native_calls) > 1:
            raise GroqStructuredResponseError(
                "multiple Tool calls are not supported in one structured turn"
            )
        if native_calls:
            native = native_calls[0]
            call_id = getattr(native, "id", None)
            if not isinstance(call_id, str) or not call_id:
                raise GroqStructuredResponseError(
                    "Groq Tool call is missing its native id"
                )
            try:
                name = native.function.name
                arguments = native.function.arguments
            except AttributeError:
                raise GroqStructuredResponseError(
                    "Groq returned a malformed Tool call"
                ) from None
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (TypeError, ValueError):
                    raise GroqStructuredResponseError(
                        "Groq returned invalid Tool arguments"
                    ) from None
            if not isinstance(arguments, dict):
                raise GroqStructuredResponseError(
                    "Groq returned non-object Tool arguments"
                )
            tool_call = ToolCall(
                id=call_id,
                index=getattr(native, "index", None),
                name=name,
                arguments=arguments,
            )
            self._messages.append(
                {
                    "role": "assistant",
                    "content": getattr(message, "content", None),
                    "tool_calls": [_groq_tool_call(tool_call)],
                }
            )
            self._pending_tool_call = tool_call
            return ToolRequestTurn(tool_call)

        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise GroqStructuredResponseError(
                "Groq returned neither content nor a Tool call"
            )
        return FinalResultTurn(content)

    def _append_tool_result_acknowledgement(self, call: ToolCall) -> None:
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(
                    {
                        "status": "AUTHORIZED_CONTEXT_UPDATED",
                        "instruction": (
                            "Use the complete authorized context in the next "
                            "user message."
                        ),
                    }
                ),
            }
        )

    def _reset_execution(self) -> None:
        self._messages = [
            {"role": "system", "content": EXPERIMENTAL_SYSTEM_PROMPT}
        ]
        self._pending_tool_call = None
        self.request_count = 0
        self.transport_retries = 0
        self.last_transport_failure = None
        self.last_usage = GroqTokenUsage()


def _producer_input_json(producer_input: StructuredProducerInput) -> str:
    return json.dumps(
        {
            "objective": producer_input.request,
            "authorized_context": producer_input.context,
            "investigation_result_schema": producer_input.schema,
            "validation_feedback": producer_input.validation_feedback,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _groq_tools(tools: tuple[ToolDefinition, ...]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def _groq_tool_call(call: ToolCall) -> dict:
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments, ensure_ascii=False),
        },
    }


def _classify_transport_failure(error: Exception) -> GroqTransportFailure:
    status = getattr(error, "status_code", None)
    status = status if isinstance(status, int) else None
    error_name = type(error).__name__
    timed_out = error_name in {"APITimeoutError", "TimeoutException"}
    if timed_out or status == 429 or (status is not None and status >= 500):
        retryability = RetryDisposition.RETRYABLE
    elif status is not None and 400 <= status < 500:
        retryability = RetryDisposition.NON_RETRYABLE
    else:
        retryability = RetryDisposition.CONDITIONAL
    if timed_out:
        failure_type = "TIMEOUT"
    elif status == 429:
        failure_type = "RATE_LIMIT"
    elif status in {401, 403}:
        failure_type = "AUTHORIZATION"
    elif status is not None and 400 <= status < 500:
        failure_type = "CLIENT_ERROR"
    elif status is not None and status >= 500:
        failure_type = "SERVER_ERROR"
    else:
        failure_type = "CONNECTION_ERROR"
    return GroqTransportFailure(
        category=ProducerFailureCategory.TRANSPORT_PROVIDER_FAILURE,
        retryability=retryability,
        failure_type=failure_type,
        status_code=status,
        request_id=_safe_request_id(getattr(error, "request_id", None)),
    )


def _safe_request_id(value) -> str | None:
    if isinstance(value, str) and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return None


def _retry_after_seconds(error: Exception) -> float:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return 0
    try:
        value = headers.get("retry-after")
        delay = float(value)
    except (AttributeError, TypeError, ValueError):
        return 0
    return max(0, delay)


def _usage(response) -> GroqTokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return GroqTokenUsage()
    return GroqTokenUsage(
        prompt_tokens=_safe_int(getattr(usage, "prompt_tokens", None)),
        completion_tokens=_safe_int(
            getattr(usage, "completion_tokens", None)
        ),
        total_tokens=_safe_int(getattr(usage, "total_tokens", None)),
    )


def _safe_int(value) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
