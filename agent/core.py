import json
from dataclasses import asdict, dataclass

from knowledge import retrieval

from .contracts import LLMMessage
from .errors import AgentAuthorizationError
from .factory import get_active_provider
from .models import AgentInteraction
from .tools import ToolContext, ToolRegistry


MAX_TOOL_ROUNDS = 5

SYSTEM_PROMPT = """Você é o assistente investigativo do FaultTrace.
Ajude o técnico a investigar informação sobre a falha; o diagnóstico final pertence ao técnico.
Use as Tools quando precisar de histórico, documentação ou detalhes que não estão no contexto inicial.
Diferencie fatos atuais de histórico: casos semelhantes não provam a causa atual e documentação é evidência técnica, não confirmação automática.
Preserve as referências das fontes recuperadas, procure sustentação e contradições, explicite incertezas e não invente informação ausente.
Na resposta final, use as seções: Resumo da investigação; O que sabemos; Evidências relevantes; Contradições; Hipóteses relacionadas; O que ainda falta verificar; Fontes consultadas.
Seja conciso. Nunca confirme hipótese, causa ou diagnóstico em nome do técnico."""


@dataclass(frozen=True)
class AgentResult:
    content: str
    provider: str
    model: str
    tools_used: tuple[dict, ...]
    sources: tuple[str, ...]
    status: str = "COMPLETED"


def _initial_context(investigation):
    organization = investigation.incident.organization
    incident = retrieval.get_incident_details(
        organization=organization,
        incident_id=investigation.incident_id,
    )
    equipment = retrieval.get_equipment_context(
        organization=organization,
        equipment_id=investigation.incident.equipment_id,
    )
    equipment_data = asdict(equipment)
    equipment_data.pop("documents", None)
    return {
        "incident": asdict(incident.incident),
        "equipment": equipment_data,
        "investigation": incident.investigation,
        "facts": incident.facts,
        "evidence": incident.evidence,
        "hypotheses": incident.hypotheses,
    }


class InvestigationAgent:
    def __init__(self, *, registry=None, max_tool_rounds=MAX_TOOL_ROUNDS):
        self.registry = registry or ToolRegistry()
        self.max_tool_rounds = max_tool_rounds

    def run(self, *, investigation, user, question, provider=None):
        if user.organization_id != investigation.incident.organization_id:
            raise AgentAuthorizationError(
                "O usuário e a investigação devem pertencer à mesma organização."
            )
        question = question.strip()
        if not question:
            raise ValueError("Informe o que deve ser investigado.")

        provider = provider or get_active_provider(investigation.incident.organization)
        messages = [
            LLMMessage(role="system", content=SYSTEM_PROMPT),
            LLMMessage(
                role="user",
                content=(
                    "Contexto atual autorizado:\n"
                    + json.dumps(
                        _initial_context(investigation),
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n\nSolicitação do técnico:\n"
                    + question
                ),
            ),
        ]
        tool_context = ToolContext(
            organization=investigation.incident.organization,
            incident_id=investigation.incident_id,
            equipment_id=investigation.incident.equipment_id,
        )
        trace = []
        sources = []
        tool_rounds = 0

        while True:
            response = provider.generate(
                messages,
                tools=self.registry.definitions,
                max_tokens=2400,
            )
            if not response.tool_calls:
                result = AgentResult(
                    content=response.content,
                    provider=response.provider,
                    model=response.model,
                    tools_used=tuple(trace),
                    sources=tuple(sources),
                )
                self._persist(investigation, user, question, result)
                return result

            if tool_rounds >= self.max_tool_rounds:
                result = AgentResult(
                    content=(
                        "A investigação foi interrompida ao atingir o limite seguro de "
                        f"{self.max_tool_rounds} rodadas de Tools. Reformule a pergunta "
                        "ou revise os resultados já recuperados."
                    ),
                    provider=response.provider,
                    model=response.model,
                    tools_used=tuple(trace),
                    sources=tuple(sources),
                    status="TOOL_LIMIT_REACHED",
                )
                self._persist(investigation, user, question, result)
                return result

            tool_rounds += 1
            messages.append(
                LLMMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                execution = self.registry.execute(call, tool_context)
                for source in execution.sources:
                    if source not in sources:
                        sources.append(source)
                trace.append(
                    {
                        "round": tool_rounds,
                        "name": call.name,
                        "arguments": call.arguments,
                        "ok": execution.ok,
                    }
                )
                messages.append(
                    LLMMessage(
                        role="tool",
                        content=json.dumps(
                            execution.as_payload(), ensure_ascii=False, default=str
                        ),
                        tool_call_id=call.id,
                        tool_name=call.name,
                    )
                )

    @staticmethod
    def _persist(investigation, user, question, result):
        AgentInteraction.objects.create(
            investigation=investigation,
            user=user,
            question=question,
            response=result.content,
            provider=result.provider,
            model=result.model,
            tools_used=list(result.tools_used),
            sources=list(result.sources),
        )
