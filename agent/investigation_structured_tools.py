"""Allow-listed Tool surface for the experimental structured flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from knowledge import retrieval
from maintenance.models import Incident

from .contracts import ToolCall, ToolDefinition
from .investigation_context import InvestigationContext
from .investigation_context_adapter import (
    InvestigationContextAdapterError,
    expand_with_documentation,
    expand_with_equipment_history,
    expand_with_similar_incidents,
)


class StructuredToolError(ValueError):
    """A Tool request is unknown, invalid, or unauthorized."""


@dataclass(frozen=True)
class StructuredToolAudit:
    round_number: int
    name: str
    ok: bool


@dataclass(frozen=True)
class _RegisteredStructuredTool:
    definition: ToolDefinition
    expand: Callable[[InvestigationContext, Incident, dict], InvestigationContext]


def _object_schema(properties=None, required=()):
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


def _history(context, incident, arguments):
    return expand_with_equipment_history(context, incident=incident)


def _similar(context, incident, arguments):
    return expand_with_similar_incidents(
        context,
        incident=incident,
        query=arguments["query"],
    )


def _documentation(context, incident, arguments):
    return expand_with_documentation(
        context,
        incident=incident,
        query=arguments["query"],
    )


class StructuredInvestigationToolRegistry:
    """Validates semantic requests and expands context through its adapter."""

    def __init__(self):
        query = {
            "query": {"type": "string", "minLength": 1, "maxLength": 500}
        }
        tools = (
            _RegisteredStructuredTool(
                ToolDefinition(
                    name="search_equipment_history",
                    description=(
                        "Recupera ocorrências anteriores do equipamento atual. "
                        "Não aceita identificadores."
                    ),
                    input_schema=_object_schema(),
                ),
                _history,
            ),
            _RegisteredStructuredTool(
                ToolDefinition(
                    name="search_similar_incidents",
                    description=(
                        "Busca incidentes similares autorizados por termos técnicos."
                    ),
                    input_schema=_object_schema(query, ("query",)),
                ),
                _similar,
            ),
            _RegisteredStructuredTool(
                ToolDefinition(
                    name="search_documentation",
                    description=(
                        "Busca seções documentais vinculadas ao equipamento atual."
                    ),
                    input_schema=_object_schema(query, ("query",)),
                ),
                _documentation,
            ),
        )
        self._tools = {tool.definition.name: tool for tool in tools}

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool.definition for tool in self._tools.values())

    def execute(
        self,
        call: ToolCall,
        *,
        context: InvestigationContext,
        incident: Incident,
    ) -> InvestigationContext:
        if not isinstance(call, ToolCall):
            raise StructuredToolError("invalid Tool request")
        tool = self._tools.get(call.name)
        if tool is None:
            raise StructuredToolError("Tool is not allowed")
        _validate_arguments(call.arguments, tool.definition.input_schema)
        try:
            return tool.expand(context, incident, call.arguments)
        except (
            InvestigationContextAdapterError,
            retrieval.RetrievalNotFound,
        ) as error:
            raise StructuredToolError(
                "Tool result failed context authorization"
            ) from error
        except Exception as error:
            raise StructuredToolError("Tool execution failed") from error


def _validate_arguments(arguments, schema):
    if not isinstance(arguments, dict):
        raise StructuredToolError("Tool arguments must be an object")
    properties = schema["properties"]
    unknown = set(arguments) - set(properties)
    if unknown:
        raise StructuredToolError("Tool contains unexpected arguments")
    missing = set(schema["required"]) - set(arguments)
    if missing:
        raise StructuredToolError("Tool is missing required arguments")
    for name, value in arguments.items():
        expected = properties[name]["type"]
        if expected == "string":
            if not isinstance(value, str) or not value.strip():
                raise StructuredToolError(f"{name} must be non-empty text")
            if len(value) > properties[name].get("maxLength", 500):
                raise StructuredToolError(f"{name} is too long")
