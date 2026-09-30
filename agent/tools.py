from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Callable

from knowledge import retrieval

from .contracts import ToolCall, ToolDefinition
from .errors import AgentExecutionError


@dataclass(frozen=True)
class ToolContext:
    organization: Any
    incident_id: int
    equipment_id: int


@dataclass(frozen=True)
class ToolExecution:
    name: str
    ok: bool
    result: Any = None
    error: str = ""
    sources: tuple[str, ...] = ()

    def as_payload(self):
        payload = {"ok": self.ok}
        if self.ok:
            payload["result"] = _plain(self.result)
        else:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    executor: Callable[[ToolContext, dict[str, Any]], Any]


class ToolValidationError(Exception):
    pass


def _plain(value):
    if is_dataclass(value):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _sources(value):
    found = []

    def visit(item):
        if is_dataclass(item):
            visit(asdict(item))
        elif isinstance(item, dict):
            reference = item.get("reference")
            if isinstance(reference, str) and reference and reference not in found:
                found.append(reference)
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return tuple(found)


def _object_schema(properties=None, required=()):
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


def _validate(arguments, schema):
    if not isinstance(arguments, dict):
        raise ToolValidationError("Os argumentos devem formar um objeto.")
    properties = schema["properties"]
    unknown = set(arguments) - set(properties)
    if unknown:
        raise ToolValidationError(
            f"Argumentos não permitidos: {', '.join(sorted(unknown))}."
        )
    missing = set(schema["required"]) - set(arguments)
    if missing:
        raise ToolValidationError(
            f"Argumentos obrigatórios ausentes: {', '.join(sorted(missing))}."
        )
    for name, value in arguments.items():
        expected = properties[name]["type"]
        if expected == "string" and (not isinstance(value, str) or not value.strip()):
            raise ToolValidationError(f"{name} deve ser um texto não vazio.")
        if expected == "string" and len(value) > properties[name].get("maxLength", 500):
            raise ToolValidationError(f"{name} excede o tamanho permitido.")
        if expected == "integer" and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            raise ToolValidationError(f"{name} deve ser um inteiro positivo.")


def _equipment_context(context, arguments):
    return retrieval.get_equipment_context(
        organization=context.organization,
        equipment_id=context.equipment_id,
    )


def _equipment_history(context, arguments):
    return retrieval.search_equipment_history(
        organization=context.organization,
        equipment_id=context.equipment_id,
        exclude_incident_id=context.incident_id,
    )


def _similar_incidents(context, arguments):
    return retrieval.search_similar_incidents(
        organization=context.organization,
        query=arguments["query"],
        current_incident_id=context.incident_id,
    )


def _incident_details(context, arguments):
    return retrieval.get_incident_details(
        organization=context.organization,
        incident_id=arguments["incident_id"],
    )


def _documentation(context, arguments):
    return retrieval.search_documentation(
        organization=context.organization,
        equipment_id=context.equipment_id,
        query=arguments["query"],
    )


class ToolRegistry:
    def __init__(self):
        query = {"query": {"type": "string", "minLength": 1, "maxLength": 500}}
        self._tools = {
            tool.definition.name: tool
            for tool in (
                RegisteredTool(
                    ToolDefinition(
                        name="get_equipment_context",
                        description=(
                            "Obtém identificação, características e documentos vinculados "
                            "ao equipamento desta investigação."
                        ),
                        input_schema=_object_schema(),
                    ),
                    _equipment_context,
                ),
                RegisteredTool(
                    ToolDefinition(
                        name="search_equipment_history",
                        description=(
                            "Lista ocorrências anteriores do equipamento atual, com "
                            "intervenções e causas confirmadas por técnicos."
                        ),
                        input_schema=_object_schema(),
                    ),
                    _equipment_history,
                ),
                RegisteredTool(
                    ToolDefinition(
                        name="search_similar_incidents",
                        description=(
                            "Busca ocorrências anteriores semelhantes na organização por "
                            "termos técnicos. Histórico semelhante não confirma a causa atual."
                        ),
                        input_schema=_object_schema(query, ("query",)),
                    ),
                    _similar_incidents,
                ),
                RegisteredTool(
                    ToolDefinition(
                        name="get_incident_details",
                        description=(
                            "Obtém fatos, evidências, hipóteses e intervenções de uma "
                            "ocorrência autorizada encontrada nas buscas."
                        ),
                        input_schema=_object_schema(
                            {"incident_id": {"type": "integer", "minimum": 1}},
                            ("incident_id",),
                        ),
                    ),
                    _incident_details,
                ),
                RegisteredTool(
                    ToolDefinition(
                        name="search_documentation",
                        description=(
                            "Pesquisa trechos da documentação vinculada ao equipamento "
                            "usando termos técnicos e retorna a página de origem."
                        ),
                        input_schema=_object_schema(query, ("query",)),
                    ),
                    _documentation,
                ),
            )
        }

    @property
    def definitions(self):
        return tuple(tool.definition for tool in self._tools.values())

    def execute(self, call: ToolCall, context: ToolContext):
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolExecution(
                name=call.name,
                ok=False,
                error="Tool não permitida para esta investigação.",
            )
        try:
            _validate(call.arguments, tool.definition.input_schema)
            result = tool.executor(context, call.arguments)
        except ToolValidationError as error:
            return ToolExecution(name=call.name, ok=False, error=str(error))
        except retrieval.RetrievalNotFound as error:
            return ToolExecution(name=call.name, ok=False, error=str(error))
        except Exception as error:
            raise AgentExecutionError(
                f"Falha interna ao executar a Tool {call.name}."
            ) from error
        return ToolExecution(
            name=call.name,
            ok=True,
            result=result,
            sources=_sources(result),
        )
