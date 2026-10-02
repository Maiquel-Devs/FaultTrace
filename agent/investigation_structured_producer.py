"""Provider-independent boundary for untrusted structured producers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .contracts import ToolCall, ToolDefinition


class StructuredProducerError(RuntimeError):
    """Controlled failure while obtaining a producer turn."""


@dataclass(frozen=True)
class StructuredProducerInput:
    request: str
    context: dict[str, Any]
    schema: dict[str, Any]
    tools: tuple[ToolDefinition, ...]
    round_number: int
    validation_feedback: dict[str, str] | None = None


@dataclass(frozen=True)
class ToolRequestTurn:
    tool_call: ToolCall


@dataclass(frozen=True)
class FinalResultTurn:
    raw_output: str

    def __post_init__(self):
        if not isinstance(self.raw_output, str):
            raise TypeError("raw_output must be a string")


ProducerTurn = ToolRequestTurn | FinalResultTurn


class StructuredInvestigationProducer(ABC):
    """Produces untrusted turns; validation remains application-owned."""

    @abstractmethod
    def produce(self, producer_input: StructuredProducerInput) -> ProducerTurn:
        raise NotImplementedError


class FakeStructuredProducer(StructuredInvestigationProducer):
    """Deterministic scripted producer used only by tests/experiments."""

    def __init__(self, turns: list[ProducerTurn | Exception]):
        self._turns = list(turns)
        self.inputs: list[StructuredProducerInput] = []

    def produce(self, producer_input: StructuredProducerInput) -> ProducerTurn:
        self.inputs.append(producer_input)
        if not self._turns:
            raise StructuredProducerError("fake producer has no scripted turn")
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        if not isinstance(turn, (ToolRequestTurn, FinalResultTurn)):
            raise StructuredProducerError("fake producer received an invalid script")
        return turn
