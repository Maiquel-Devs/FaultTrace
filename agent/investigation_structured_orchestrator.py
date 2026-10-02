"""Experimental structured investigation cycle, parallel to the legacy Agent."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from maintenance.models import Incident

from .investigation_context import InvestigationContext
from .investigation_context_adapter import build_authorized_investigation_context
from .investigation_contracts import (
    ContractParseError,
    ContractValidationError,
    InvestigationResultV1,
    parse_investigation_result_json,
)
from .investigation_producer_policy import (
    ProducerFailureCategory,
    RetryDisposition,
    classify_contract_exception,
    recommended_retry,
    safe_validation_feedback,
)
from .investigation_schema import investigation_result_v1_schema
from .investigation_structured_producer import (
    FinalResultTurn,
    StructuredInvestigationProducer,
    StructuredProducerInput,
    ToolRequestTurn,
)
from .investigation_structured_tools import (
    StructuredInvestigationToolRegistry,
    StructuredToolAudit,
    StructuredToolError,
)


# Matches the legacy Agent's verified safety policy without importing provider
# or persistence dependencies from agent.core.
DEFAULT_MAX_TOOL_ROUNDS = 5
MAX_VALIDATION_RETRIES = 1


class StructuredExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    TOOL_LIMIT_REACHED = "TOOL_LIMIT_REACHED"
    TOOL_ERROR = "TOOL_ERROR"
    PRODUCER_ERROR = "PRODUCER_ERROR"


@dataclass(frozen=True)
class ValidationFailureAudit:
    attempt: int
    category: ProducerFailureCategory
    path: str | None


@dataclass(frozen=True)
class StructuredInvestigationExecution:
    status: StructuredExecutionStatus
    result: InvestigationResultV1 | None
    context: InvestigationContext
    rounds: int
    tool_calls: tuple[StructuredToolAudit, ...]
    validation_attempts: int
    validation_failures: tuple[ValidationFailureAudit, ...]

    @property
    def retry_count(self) -> int:
        return max(0, self.validation_attempts - 1)


class StructuredInvestigationOrchestrator:
    def __init__(
        self,
        *,
        producer: StructuredInvestigationProducer,
        tools: StructuredInvestigationToolRegistry | None = None,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
        max_validation_retries: int = MAX_VALIDATION_RETRIES,
    ):
        if not isinstance(producer, StructuredInvestigationProducer):
            raise TypeError("producer must implement StructuredInvestigationProducer")
        if not isinstance(max_tool_rounds, int) or max_tool_rounds < 0:
            raise ValueError("max_tool_rounds must be a non-negative integer")
        if max_validation_retries not in (0, MAX_VALIDATION_RETRIES):
            raise ValueError("Phase 3 permits at most one validation retry")
        self.producer = producer
        self.tools = tools or StructuredInvestigationToolRegistry()
        self.max_tool_rounds = max_tool_rounds
        self.max_validation_retries = max_validation_retries

    def run(
        self,
        *,
        incident: Incident,
        request: str,
    ) -> StructuredInvestigationExecution:
        if not isinstance(request, str) or not request.strip():
            raise ValueError("request must be non-empty text")

        context = build_authorized_investigation_context(incident=incident)
        rounds = 0
        tool_rounds = 0
        validation_attempts = 0
        tool_audit: list[StructuredToolAudit] = []
        validation_audit: list[ValidationFailureAudit] = []
        feedback = None

        while True:
            producer_input = StructuredProducerInput(
                request=request.strip(),
                context=context.to_producer_dict(),
                schema=investigation_result_v1_schema(),
                tools=self.tools.definitions,
                round_number=rounds + 1,
                validation_feedback=feedback,
            )
            try:
                turn = self.producer.produce(producer_input)
            except Exception:
                return self._execution(
                    StructuredExecutionStatus.PRODUCER_ERROR,
                    context,
                    rounds + 1,
                    tool_audit,
                    validation_attempts,
                    validation_audit,
                )
            rounds += 1

            if isinstance(turn, ToolRequestTurn):
                if tool_rounds >= self.max_tool_rounds:
                    tool_audit.append(
                        StructuredToolAudit(
                            round_number=rounds,
                            name=turn.tool_call.name,
                            ok=False,
                        )
                    )
                    return self._execution(
                        StructuredExecutionStatus.TOOL_LIMIT_REACHED,
                        context,
                        rounds,
                        tool_audit,
                        validation_attempts,
                        validation_audit,
                    )
                try:
                    context = self.tools.execute(
                        turn.tool_call,
                        context=context,
                        incident=incident,
                    )
                except StructuredToolError:
                    tool_audit.append(
                        StructuredToolAudit(
                            round_number=rounds,
                            name=turn.tool_call.name,
                            ok=False,
                        )
                    )
                    return self._execution(
                        StructuredExecutionStatus.TOOL_ERROR,
                        context,
                        rounds,
                        tool_audit,
                        validation_attempts,
                        validation_audit,
                    )
                tool_rounds += 1
                tool_audit.append(
                    StructuredToolAudit(
                        round_number=rounds,
                        name=turn.tool_call.name,
                        ok=True,
                    )
                )
                continue

            if not isinstance(turn, FinalResultTurn):
                return self._execution(
                    StructuredExecutionStatus.PRODUCER_ERROR,
                    context,
                    rounds,
                    tool_audit,
                    validation_attempts,
                    validation_audit,
                )

            validation_attempts += 1
            try:
                result = parse_investigation_result_json(
                    turn.raw_output,
                    source_catalog=context.source_catalog,
                    registered_hypothesis_catalog=(
                        context.registered_hypothesis_catalog
                    ),
                )
            except (ContractParseError, ContractValidationError) as error:
                category = classify_contract_exception(error)
                path = getattr(error, "path", None)
                validation_audit.append(
                    ValidationFailureAudit(
                        attempt=validation_attempts,
                        category=category,
                        path=path if isinstance(path, str) else None,
                    )
                )
                disposition = recommended_retry(
                    category,
                    failures_so_far=validation_attempts,
                )
                if (
                    disposition is RetryDisposition.CONDITIONAL
                    and validation_attempts <= self.max_validation_retries
                ):
                    feedback = safe_validation_feedback(error)
                    continue
                return self._execution(
                    StructuredExecutionStatus.VALIDATION_FAILED,
                    context,
                    rounds,
                    tool_audit,
                    validation_attempts,
                    validation_audit,
                )

            return StructuredInvestigationExecution(
                status=StructuredExecutionStatus.SUCCESS,
                result=result,
                context=context,
                rounds=rounds,
                tool_calls=tuple(tool_audit),
                validation_attempts=validation_attempts,
                validation_failures=tuple(validation_audit),
            )

    @staticmethod
    def _execution(
        status,
        context,
        rounds,
        tool_audit,
        validation_attempts,
        validation_audit,
    ) -> StructuredInvestigationExecution:
        return StructuredInvestigationExecution(
            status=status,
            result=None,
            context=context,
            rounds=rounds,
            tool_calls=tuple(tool_audit),
            validation_attempts=validation_attempts,
            validation_failures=tuple(validation_audit),
        )
