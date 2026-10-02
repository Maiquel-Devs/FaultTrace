"""Synthetic context shaped after FaultTrace retrieval and domain objects."""

import json
from copy import deepcopy

from agent.investigation_context import (
    RegisteredHypothesisCandidate,
    SourceCandidate,
    SourceKind,
    build_investigation_context,
)


def _content(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


SOURCE_CANDIDATES = (
    SourceCandidate(
        internal_locator="maintenance.Incident:4102",
        kind=SourceKind.CURRENT_INCIDENT,
        authorized_content=_content(
            {
                "description": "Unidade interrompe a operação após período prolongado.",
                "status": "UNDER_INVESTIGATION",
                "occurred_at": "2026-01-15T10:30:00-03:00",
            }
        ),
        display_label="Ocorrência atual",
    ),
    SourceCandidate(
        internal_locator="assets.Equipment:730",
        kind=SourceKind.EQUIPMENT,
        authorized_content=_content(
            {
                "code": "EQ-DEMO",
                "manufacturer": "Fabricante fictício",
                "model": "Modelo demonstrativo",
                "location": "Linha de testes",
                "status": "ACTIVE",
            }
        ),
        display_label="EQ-DEMO — Unidade de teste",
    ),
    SourceCandidate(
        internal_locator="maintenance.Investigation:880",
        kind=SourceKind.INVESTIGATION,
        authorized_content=_content(
            {
                "status": "ACTIVE",
                "started_at": "2026-01-15T11:00:00-03:00",
            }
        ),
        display_label="Investigação atual",
    ),
    SourceCandidate(
        internal_locator="knowledge.Fact:901",
        kind=SourceKind.FACT,
        authorized_content=_content(
            {
                "content": "Código de proteção observado durante a interrupção.",
                "source_type": "TECHNICIAN",
            }
        ),
        display_label="Fato registrado pelo técnico",
    ),
    SourceCandidate(
        internal_locator="knowledge.Evidence:903",
        kind=SourceKind.EVIDENCE,
        authorized_content=_content(
            {
                "content": "Medição operacional ficou acima da referência.",
                "source_type": "TECHNICIAN",
                "source_reference": "Medição controlada",
            }
        ),
        display_label="Evidência registrada — medição controlada",
    ),
    SourceCandidate(
        internal_locator="knowledge.Evidence:904",
        kind=SourceKind.EVIDENCE,
        authorized_content=_content(
            {
                "content": "Inspeção visual não identificou bloqueio aparente.",
                "source_type": "TECHNICIAN",
                "source_reference": "Inspeção atual",
            }
        ),
        display_label="Evidência registrada — inspeção atual",
    ),
    SourceCandidate(
        internal_locator="maintenance.Incident:3990",
        kind=SourceKind.HISTORICAL_INCIDENT,
        authorized_content=_content(
            {
                "description": "Evento anterior apresentou sintoma semelhante.",
                "status": "RESOLVED",
                "occurred_at": "2025-11-10T09:00:00-03:00",
            }
        ),
        display_label="Ocorrência histórica autorizada",
    ),
    SourceCandidate(
        internal_locator="maintenance.Intervention:602",
        kind=SourceKind.INTERVENTION,
        authorized_content=_content(
            {
                "action_taken": "Componente obstruído foi limpo.",
                "confirmed_cause": "Restrição de fluxo confirmada pelo técnico.",
                "result": "Operação normalizada após a intervenção.",
            }
        ),
        display_label="Intervenção da ocorrência histórica",
    ),
    SourceCandidate(
        internal_locator="assets.Document:220",
        kind=SourceKind.DOCUMENT,
        authorized_content=_content(
            {
                "title": "Manual técnico fictício",
                "description": "Procedimentos de diagnóstico da unidade de teste.",
            }
        ),
        display_label="Manual técnico fictício",
    ),
    SourceCandidate(
        internal_locator="knowledge.DocumentSection:220:12",
        kind=SourceKind.DOCUMENT_SECTION,
        authorized_content=(
            "A proteção pode atuar diante de condição operacional anormal; "
            "verificar medições, elemento sensor e condição de fluxo."
        ),
        display_label="Manual técnico fictício — página 12",
    ),
)


REGISTERED_HYPOTHESIS_CANDIDATES = (
    RegisteredHypothesisCandidate(
        internal_locator="knowledge.Hypothesis:950",
        statement="Condição operacional anormal durante o ciclo prolongado.",
        observed_status="ACTIVE",
        supporting_source_locators=("knowledge.Evidence:903",),
        opposing_source_locators=("knowledge.Evidence:904",),
    ),
)


REALISTIC_CONTEXT = build_investigation_context(
    sources=SOURCE_CANDIDATES,
    registered_hypotheses=REGISTERED_HYPOTHESIS_CANDIDATES,
)

SOURCE_REFS_BY_LOCATOR = {
    source.internal_locator: source.ref for source in REALISTIC_CONTEXT.sources
}
HYPOTHESIS_REFS_BY_LOCATOR = {
    hypothesis.internal_locator: hypothesis.ref
    for hypothesis in REALISTIC_CONTEXT.registered_hypotheses
}


SIMULATED_RESULT_PAYLOAD = {
    "schema_version": "1.0",
    "problem": {
        "statement": "A unidade interrompe a operação após período prolongado.",
        "source_refs": [SOURCE_REFS_BY_LOCATOR["maintenance.Incident:4102"]],
    },
    "summary": (
        "A proteção atuou sob condição operacional anormal; a origem permanece "
        "em investigação."
    ),
    "known_facts": [
        {
            "id": "fact_1",
            "statement": "Um código de proteção foi observado na interrupção.",
            "source_refs": [SOURCE_REFS_BY_LOCATOR["knowledge.Fact:901"]],
        }
    ],
    "evidence": [
        {
            "id": "evidence_1",
            "statement": "A medição operacional ficou acima da referência.",
            "significance": "A medição sustenta uma condição operacional anormal.",
            "source_refs": [SOURCE_REFS_BY_LOCATOR["knowledge.Evidence:903"]],
        },
        {
            "id": "evidence_2",
            "statement": "A documentação indica mais de uma origem possível.",
            "significance": "O evento não deve ser atribuído a uma causa sem testes.",
            "source_refs": [
                SOURCE_REFS_BY_LOCATOR["knowledge.DocumentSection:220:12"]
            ],
        },
    ],
    "contradictions": [],
    "hypotheses": [
        {
            "id": "hypothesis_1",
            "origin": "REGISTERED",
            "registered_ref": HYPOTHESIS_REFS_BY_LOCATOR[
                "knowledge.Hypothesis:950"
            ],
            "statement": (
                "Condição operacional anormal durante o ciclo prolongado."
            ),
            "evidence_assessment": "MIXED",
            "supporting_finding_refs": ["evidence_1"],
            "opposing_finding_refs": ["evidence_2"],
            "rationale": (
                "A medição sustenta a condição, mas a documentação mantém "
                "origens alternativas abertas."
            ),
        }
    ],
    "checks": [
        {
            "action": "Correlacionar a medição com o momento de atuação da proteção.",
            "reason": "A correlação ajuda a distinguir condição persistente de evento isolado.",
            "basis_refs": ["fact_1", "evidence_1", "hypothesis_1"],
            "possible_outcomes": [],
        }
    ],
}


SIMULATED_RESULT_JSON = json.dumps(
    SIMULATED_RESULT_PAYLOAD,
    ensure_ascii=False,
)


def simulated_payload():
    return deepcopy(SIMULATED_RESULT_PAYLOAD)
