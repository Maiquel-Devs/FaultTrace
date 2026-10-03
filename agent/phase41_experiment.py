"""One-shot, rollback-only real-provider experiment for FaultTrace Phase 4.1.

Run only after the local mocked test gate.  Parsed results and safe metrics may
be written to a JSON report; provider raw output is never persisted.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts.models import Organization, User
from assets.models import Document, Equipment
from config.models import AIConfiguration
from knowledge.models import (
    DocumentSection,
    Evidence,
    Fact,
    Hypothesis,
    HypothesisEvidence,
)
from maintenance.models import Incident, Investigation

from .investigation_context import SourceKind
from .investigation_groq_producer import GroqStructuredInvestigationProducer
from .investigation_structured_orchestrator import StructuredInvestigationOrchestrator
from .investigation_structured_producer import ToolRequestTurn
from .investigation_technical_corpus import (
    R2_EVALUATION_CASE,
    TECHNICAL_EVALUATION_CASES,
    TechnicalEvaluationCase,
)
from .investigation_technical_evaluation import evaluate_automatic
from .models import AgentInteraction


EXPECTED_PROVIDER = AIConfiguration.Provider.GROQ
EXPECTED_MODEL = "openai/gpt-oss-120b"


@dataclass(frozen=True)
class MaterializedCase:
    incident: Incident
    locator_aliases: dict[str, str]


class ObservedGroqProducer(GroqStructuredInvestigationProducer):
    """Collect safe experiment telemetry around the unchanged adapter."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.observed_inputs = []
        self.observed_tools = []
        self.usage_history = []

    def produce(self, producer_input):
        self.observed_inputs.append(producer_input)
        turn = super().produce(producer_input)
        self.usage_history.append(self.last_usage)
        if isinstance(turn, ToolRequestTurn):
            self.observed_tools.append(
                {
                    "round": producer_input.round_number,
                    "name": turn.tool_call.name,
                    "arguments": dict(turn.tool_call.arguments),
                }
            )
        return turn


def run_phase41_experiment(report_path: str | Path) -> dict[str, Any]:
    """Run R2 then T1-T5 once each, stopping immediately after a 429."""

    configuration = (
        AIConfiguration.objects.filter(is_active=True)
        .order_by("pk")
        .first()
    )
    if configuration is None:
        raise RuntimeError("No active AI configuration is available.")
    if configuration.provider != EXPECTED_PROVIDER:
        raise RuntimeError("The active provider is not GROQ.")
    if configuration.model != EXPECTED_MODEL:
        raise RuntimeError("The configured model is not the audited Phase 4 model.")

    interactions_before = AgentInteraction.objects.count()
    started = time.perf_counter()
    records = []
    stopped_for_rate_limit = False
    with transaction.atomic():
        for case in (R2_EVALUATION_CASE, *TECHNICAL_EVALUATION_CASES):
            record = _run_case(case, configuration)
            records.append(record)
            if record["rate_limited"]:
                stopped_for_rate_limit = True
                break
        transaction.set_rollback(True)

    interactions_after = AgentInteraction.objects.count()
    report = {
        "phase": "4.1",
        "provider": configuration.provider,
        "model": configuration.model,
        "execution_order": [item["case_id"] for item in records],
        "stopped_for_rate_limit": stopped_for_rate_limit,
        "agent_interactions_before": interactions_before,
        "agent_interactions_after": interactions_after,
        "database_results_persisted": False,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "cases": records,
    }
    path = Path(report_path)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def _run_case(case, configuration):
    materialized = materialize_case(case)
    producer = ObservedGroqProducer(
        api_key=configuration.get_api_key(),
        model=configuration.model,
    )
    started = time.perf_counter()
    execution = StructuredInvestigationOrchestrator(producer=producer).run(
        incident=materialized.incident,
        request=case.model_input.request,
    )
    duration = round(time.perf_counter() - started, 3)

    inputs = producer.observed_inputs
    refs_by_round = [
        [source["ref"] for source in item.context["sources"]]
        for item in inputs
    ]
    added_refs = (
        [ref for ref in refs_by_round[1] if ref not in refs_by_round[0]]
        if len(refs_by_round) > 1
        else []
    )
    context = execution.context
    document_refs = [
        source.ref
        for source in context.sources
        if source.kind is SourceKind.DOCUMENT_SECTION
    ]
    cited_refs = (
        list(context.cited_source_refs(execution.result))
        if execution.result is not None
        else []
    )
    automatic = (
        evaluate_automatic(
            case,
            context,
            execution.result,
            locator_aliases=materialized.locator_aliases,
        )
        if execution.result is not None
        else None
    )
    usage = _aggregate_usage(producer.usage_history)
    failure = producer.last_transport_failure
    return {
        "case_id": case.case_id,
        "title": case.title,
        "domain": case.model_input.domain,
        "problem_input": case.model_input.incident_description,
        "provided_facts": [item.content for item in case.model_input.facts],
        "provided_evidence": [item.content for item in case.model_input.evidence],
        "trap": list(case.expectations.expected_reasoning_properties),
        "expected_behavior": list(case.expectations.expected_check_properties),
        "orchestrator_status": execution.status.value,
        "rounds": execution.rounds,
        "tools": producer.observed_tools,
        "refs_by_round": refs_by_round,
        "refs_append_only": _refs_are_append_only(refs_by_round),
        "new_refs_after_first_tool": added_refs,
        "document_refs": document_refs,
        "document_ref_used": bool(set(document_refs) & set(cited_refs)),
        "cited_refs": cited_refs,
        "validation_retries": sum(
            item.validation_feedback is not None for item in inputs
        ),
        "validation_attempts": execution.validation_attempts,
        "validation_failures": [
            _validation_failure_record(item)
            for item in execution.validation_failures
        ],
        "transport_retries": producer.transport_retries,
        "requests": producer.request_count,
        "tokens": usage,
        "duration_seconds": duration,
        "schema_valid": execution.result is not None,
        "automatic_evaluation": asdict(automatic) if automatic else None,
        "human_review_required": True,
        "result": execution.result.to_dict() if execution.result else None,
        "transport_failure": (
            {
                "type": failure.failure_type,
                "status": failure.status_code,
                "retryability": failure.retryability.value,
                "request_id": failure.request_id,
            }
            if failure
            else None
        ),
        "rate_limited": bool(failure and failure.failure_type == "RATE_LIMIT"),
    }


def materialize_case(case: TechnicalEvaluationCase) -> MaterializedCase:
    """Create synthetic ORM inputs inside the caller's rollback transaction."""

    stamp = f"{case.case_id}-{time.time_ns()}"
    organization = Organization.objects.create(name=f"Phase41 Synthetic {stamp}")
    user = User.objects.create_user(
        username=f"phase41-{stamp}",
        password=None,
        organization=organization,
        role=User.Role.TECHNICIAN,
    )
    data = case.model_input
    equipment = Equipment.objects.create(
        organization=organization,
        name=data.equipment_name,
        code=data.equipment_code,
        description=data.equipment_description,
    )
    incident = Incident.objects.create(
        organization=organization,
        equipment=equipment,
        description=data.incident_description,
        reported_by=user,
        occurred_at=timezone.now(),
    )
    investigation = Investigation.objects.create(
        incident=incident,
        technician=user,
    )
    aliases = {
        "incident:current": f"incident:{incident.pk}",
        "equipment:current": f"equipment:{equipment.pk}",
    }
    for fixture in data.facts:
        item = Fact.objects.create(
            investigation=investigation,
            content=fixture.content,
            source_type=Fact.SourceType.TECHNICIAN,
            created_by=user,
        )
        aliases[fixture.locator] = f"fact:{item.pk}"

    evidence_by_fixture_locator = {}
    for fixture in data.evidence:
        fields = {
            "investigation": investigation,
            "content": fixture.content,
            "source_reference": fixture.source_reference,
            "created_by": user,
        }
        if fixture.source_kind == Evidence.SourceType.PAST_INCIDENT:
            past = Incident.objects.create(
                organization=organization,
                equipment=equipment,
                description="Ocorrência histórica sintética para avaliação.",
                reported_by=user,
                occurred_at=timezone.now(),
            )
            fields.update(
                source_type=Evidence.SourceType.PAST_INCIDENT,
                source_incident=past,
            )
        else:
            fields.update(
                source_type=Evidence.SourceType.TECHNICIAN,
                source_technician=user,
            )
        item = Evidence.objects.create(**fields)
        evidence_by_fixture_locator[fixture.locator] = item
        aliases[fixture.locator] = f"evidence:{item.pk}"

    for fixture in data.hypotheses:
        item = Hypothesis.objects.create(
            investigation=investigation,
            description=fixture.statement,
            created_by=user,
        )
        aliases[fixture.locator] = f"hypothesis:{item.pk}"
        for locator in fixture.supporting_evidence_locators:
            HypothesisEvidence.objects.create(
                hypothesis=item,
                evidence=evidence_by_fixture_locator[locator],
                relation=HypothesisEvidence.Relation.SUPPORTS,
            )
        for locator in fixture.opposing_evidence_locators:
            HypothesisEvidence.objects.create(
                hypothesis=item,
                evidence=evidence_by_fixture_locator[locator],
                relation=HypothesisEvidence.Relation.CONTRADICTS,
            )

    for fixture in data.documents:
        document = Document.objects.create(
            organization=organization,
            title=fixture.title,
            file=f"documents/phase41-{stamp}.pdf",
        )
        document.equipments.add(equipment)
        DocumentSection.objects.create(
            document=document,
            page_number=fixture.page,
            content=fixture.content,
        )
        aliases[fixture.locator] = (
            f"document_section:{document.pk}:{fixture.page}"
        )
    return MaterializedCase(incident=incident, locator_aliases=aliases)


def _aggregate_usage(items):
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    return {
        field: (
            sum(getattr(item, field) for item in items if getattr(item, field) is not None)
            if any(getattr(item, field) is not None for item in items)
            else None
        )
        for field in fields
    }


def _validation_failure_record(item):
    """Serialize only the orchestrator's safe validation classification."""

    return {
        "round_number": item.round_number,
        "attempt": item.attempt,
        "category": item.category.value,
        "path": item.path,
        "instruction": item.instruction,
    }


def _refs_are_append_only(rounds):
    return all(
        current[: len(previous)] == previous
        for previous, current in zip(rounds, rounds[1:])
    )
