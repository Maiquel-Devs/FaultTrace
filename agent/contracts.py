from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResponse:
    content: str
    provider: str
    model: str


class LLMProvider(ABC):
    def __init__(self, *, api_key, model, timeout=20.0):
        self._api_key = api_key
        self.model = model
        self.timeout = timeout

    @abstractmethod
    def generate(self, messages, *, max_tokens=None):
        raise NotImplementedError

    def test_connection(self):
        self.generate(
            [LLMMessage(role="user", content="Responda apenas: OK")],
            max_tokens=32,
        )
