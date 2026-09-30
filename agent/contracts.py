from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None
    index: int | None = None
    protocol_data: Any = None


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass(frozen=True)
class LLMResponse:
    content: str
    provider: str
    model: str
    tool_calls: tuple[ToolCall, ...] = ()


class LLMProvider(ABC):
    def __init__(self, *, api_key, model, timeout=20.0):
        self._api_key = api_key
        self.model = model
        self.timeout = timeout

    @abstractmethod
    def generate(self, messages, *, tools=(), max_tokens=None):
        raise NotImplementedError

    def test_connection(self):
        self.generate(
            [LLMMessage(role="user", content="Responda apenas: OK")],
            max_tokens=32,
        )
