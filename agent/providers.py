import json

from anthropic import Anthropic
from google import genai
from google.genai import types as genai_types
from groq import Groq
from mistralai.client import Mistral
from openai import OpenAI

from .contracts import LLMMessage, LLMProvider, LLMResponse, ToolCall
from .errors import LLMProviderError


def _provider_error(provider):
    return LLMProviderError(
        f"Não foi possível comunicar com {provider}. Verifique a credencial e o modelo."
    )


def _require_response(content, tool_calls, provider):
    content = (content or "").strip()
    if not content and not tool_calls:
        raise LLMProviderError(f"{provider} retornou uma resposta vazia.")
    return content


def _arguments(value, provider):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            raise LLMProviderError(
                f"{provider} retornou argumentos de Tool inválidos."
            ) from None
    if not isinstance(value, dict):
        raise LLMProviderError(
            f"{provider} retornou argumentos de Tool inválidos."
        )
    return value


def _chat_tools(tools):
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


def _chat_messages(messages):
    provider_messages = []
    for message in messages:
        item = {"role": message.role, "content": message.content}
        if message.role == "assistant" and message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        if message.role == "tool":
            item["tool_call_id"] = message.tool_call_id
        provider_messages.append(item)
    return provider_messages


class MistralProvider(LLMProvider):
    provider_name = "MISTRAL"

    def generate(self, messages, *, tools=(), max_tokens=None):
        request = {
            "model": self.model,
            "messages": _chat_messages(messages),
            "timeout_ms": int(self.timeout * 1000),
        }
        if tools:
            request["tools"] = _chat_tools(tools)
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        try:
            with Mistral(api_key=self._api_key) as client:
                response = client.chat.complete(**request)
            response_message = response.choices[0].message
            content = response_message.content
            if isinstance(content, list):
                content = "".join(
                    getattr(part, "text", "") or "" for part in content
                )
            tool_calls = tuple(
                ToolCall(
                    id=getattr(call, "id", None),
                    name=call.function.name,
                    arguments=_arguments(call.function.arguments, "Mistral"),
                    index=getattr(call, "index", None),
                )
                for call in (getattr(response_message, "tool_calls", None) or ())
            )
            content = _require_response(content, tool_calls, "Mistral")
        except LLMProviderError:
            raise
        except Exception:
            raise _provider_error("Mistral") from None
        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=self.model,
            tool_calls=tool_calls,
        )


class GroqProvider(LLMProvider):
    provider_name = "GROQ"

    def test_connection(self):
        self.generate(
            [LLMMessage(role="user", content="Responda apenas: OK")],
            max_tokens=1024,
        )

    def generate(self, messages, *, tools=(), max_tokens=None):
        request = {
            "model": self.model,
            "messages": _chat_messages(messages),
        }
        if tools:
            request["tools"] = _chat_tools(tools)
        if max_tokens is not None:
            request["max_completion_tokens"] = max_tokens
        try:
            with Groq(
                api_key=self._api_key,
                timeout=self.timeout,
                max_retries=0,
            ) as client:
                response = client.chat.completions.create(**request)
            response_message = response.choices[0].message
            tool_calls = tuple(
                ToolCall(
                    id=getattr(call, "id", None),
                    name=call.function.name,
                    arguments=_arguments(call.function.arguments, "Groq"),
                    index=getattr(call, "index", None),
                )
                for call in (getattr(response_message, "tool_calls", None) or ())
            )
            content = _require_response(response_message.content, tool_calls, "Groq")
        except LLMProviderError:
            raise
        except Exception:
            raise _provider_error("Groq") from None
        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=self.model,
            tool_calls=tool_calls,
        )


class OpenAIProvider(LLMProvider):
    provider_name = "OPENAI"

    def generate(self, messages, *, tools=(), max_tokens=None):
        input_items = []
        for message in messages:
            if message.role == "assistant" and message.tool_calls:
                if message.content:
                    input_items.append({"role": "assistant", "content": message.content})
                input_items.extend(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    }
                    for call in message.tool_calls
                )
            elif message.role == "tool":
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content,
                    }
                )
            else:
                input_items.append(
                    {
                        "role": "developer" if message.role == "system" else message.role,
                        "content": message.content,
                    }
                )
        request = {
            "model": self.model,
            "input": input_items,
            "store": False,
        }
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                    "strict": True,
                }
                for tool in tools
            ]
        if max_tokens is not None:
            request["max_output_tokens"] = max_tokens
        try:
            with OpenAI(
                api_key=self._api_key,
                timeout=self.timeout,
                max_retries=0,
            ) as client:
                response = client.responses.create(**request)
            tool_calls = tuple(
                ToolCall(
                    id=getattr(item, "call_id", None),
                    name=item.name,
                    arguments=_arguments(item.arguments, "OpenAI"),
                    index=getattr(item, "index", None),
                )
                for item in (getattr(response, "output", None) or ())
                if getattr(item, "type", None) == "function_call"
            )
            content = _require_response(response.output_text, tool_calls, "OpenAI")
        except LLMProviderError:
            raise
        except Exception:
            raise _provider_error("OpenAI") from None
        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=self.model,
            tool_calls=tool_calls,
        )


class GeminiProvider(LLMProvider):
    provider_name = "GEMINI"

    def generate(self, messages, *, tools=(), max_tokens=None):
        system_instruction = "\n".join(
            message.content for message in messages if message.role == "system"
        )
        contents = []
        previous_was_tool = False
        for message in messages:
            if message.role == "system":
                continue
            if message.role == "assistant" and message.tool_calls:
                parts = []
                if message.content:
                    parts.append({"text": message.content})
                parts.extend(
                    {
                        "function_call": {
                            "name": call.name,
                            "args": call.arguments,
                            **({"id": call.id} if call.id else {}),
                        },
                        **(
                            {"thought_signature": call.protocol_data}
                            if call.protocol_data
                            else {}
                        ),
                    }
                    for call in message.tool_calls
                )
                contents.append({"role": "model", "parts": parts})
                previous_was_tool = False
            elif message.role == "tool":
                part = {
                    "function_response": {
                        "name": message.tool_name,
                        "response": json.loads(message.content),
                        **(
                            {"id": message.tool_call_id}
                            if message.tool_call_id
                            else {}
                        ),
                    }
                }
                if previous_was_tool:
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
                previous_was_tool = True
            else:
                contents.append(
                    {
                        "role": "model" if message.role == "assistant" else "user",
                        "parts": [{"text": message.content}],
                    }
                )
                previous_was_tool = False
        provider_tools = None
        if tools:
            provider_tools = [
                {
                    "function_declarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters_json_schema": tool.input_schema,
                        }
                        for tool in tools
                    ]
                }
            ]
        config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            max_output_tokens=max_tokens,
            tools=provider_tools,
        )
        client = None
        try:
            client = genai.Client(
                api_key=self._api_key,
                http_options=genai_types.HttpOptions(
                    timeout=int(self.timeout * 1000)
                ),
            )
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
            candidate = response.candidates[0] if response.candidates else None
            parts = candidate.content.parts if candidate and candidate.content else ()
            tool_calls = tuple(
                ToolCall(
                    id=getattr(part.function_call, "id", None),
                    name=part.function_call.name,
                    arguments=_arguments(part.function_call.args, "Google Gemini"),
                    index=index,
                    protocol_data=getattr(part, "thought_signature", None),
                )
                for index, part in enumerate(parts)
                if getattr(part, "function_call", None)
            )
            content = "".join(
                part.text for part in parts if getattr(part, "text", None)
            )
            content = _require_response(content, tool_calls, "Google Gemini")
        except LLMProviderError:
            raise
        except Exception:
            raise _provider_error("Google Gemini") from None
        finally:
            if client is not None:
                client.close()
        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=self.model,
            tool_calls=tool_calls,
        )


class AnthropicProvider(LLMProvider):
    provider_name = "ANTHROPIC"

    def generate(self, messages, *, tools=(), max_tokens=None):
        system = "\n".join(
            message.content for message in messages if message.role == "system"
        )
        provider_messages = []
        previous_was_tool = False
        for message in messages:
            if message.role == "system":
                continue
            if message.role == "assistant" and message.tool_calls:
                content = []
                if message.content:
                    content.append({"type": "text", "text": message.content})
                content.extend(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                    for call in message.tool_calls
                )
                provider_messages.append({"role": "assistant", "content": content})
                previous_was_tool = False
            elif message.role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
                if previous_was_tool:
                    provider_messages[-1]["content"].append(block)
                else:
                    provider_messages.append(
                        {"role": "user", "content": [block]}
                    )
                previous_was_tool = True
            else:
                provider_messages.append(
                    {"role": message.role, "content": message.content}
                )
                previous_was_tool = False
        request = {
            "model": self.model,
            "max_tokens": max_tokens or 1024,
            "messages": provider_messages,
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ]
        try:
            with Anthropic(
                api_key=self._api_key,
                timeout=self.timeout,
                max_retries=0,
            ) as client:
                response = client.messages.create(**request)
            tool_calls = tuple(
                ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=_arguments(block.input, "Anthropic Claude"),
                    index=index,
                )
                for index, block in enumerate(response.content)
                if block.type == "tool_use"
            )
            content = "".join(
                block.text for block in response.content if block.type == "text"
            )
            content = _require_response(content, tool_calls, "Anthropic Claude")
        except LLMProviderError:
            raise
        except Exception:
            raise _provider_error("Anthropic Claude") from None
        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=self.model,
            tool_calls=tool_calls,
        )
