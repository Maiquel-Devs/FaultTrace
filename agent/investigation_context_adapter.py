"""Read-only ORM/retrieval adapter for authorized investigation context.

This is an application boundary, not a web-permission system. The caller must
already hold an authorized root Incident. Dynamic additions are obtained only
through the existing retrieval functions scoped from that root.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from django.db.models import Prefetch, Q
from knowledge import retrieval
from knowledge.models import Evidence, Fact, Hypothesis, HypothesisEvidence
from maintenance.models import Incident, Investigation, Intervention

from .investigation_context import (
    InvestigationContext,
    RegisteredHypothesisCandidate,
    SourceCandidate,
    SourceKind,
    build_investigation_context,
    extend_investigation_context,
)


class InvestigationContextAdapterError(ValueError):
    """Base error for the authorized ORM boundary."""


class InvestigationRootNotFound(InvestigationContextAdapterError):
    """The supplied root no longer exists or has no investigation."""


class InvestigationContextAuthorizationError(InvestigationContextAdapterError):
    """An object is outside the authorized investigation universe."""


class InvestigationContextIntegrityError(InvestigationContextAdapterError):
    """Domain relationships are inconsistent for safe projection."""


@dataclass(frozen=True)
class AuthorizedContextProjectionPolicy:
    """Explicit allow-list of fields emitted for each source kind."""

    fields_by_kind: dict[SourceKind, tuple[str, ...]]


PROJECTION_POLICY = AuthorizedContextProjectionPolicy(
    fields_by_kind={
        SourceKind.CURRENT_INCIDENT: (
            "description",
            "status",
            "occurred_at",
        ),
        SourceKind.EQUIPMENT: (
            "name",
            "code",
            "manufacturer",
            "model",
            "location",
            "status",
            "description",
        ),
        SourceKind.INVESTIGATION: (
            "status",
            "started_at",
            "finished_at",
        ),
        SourceKind.FACT: ("content", "source_type"),
        SourceKind.EVIDENCE: (
            "content",
            "source_type",
            "source_reference",
        ),
        SourceKind.HISTORICAL_INCIDENT: (
            "equipment_code",
            "description",
            "status",
            "occurred_at",
        ),
        SourceKind.INTERVENTION: (
            "action_taken",
            "confirmed_cause",
            "result",
            "created_at",
        ),
        SourceKind.DOCUMENT_SECTION: (
            "document_title",
            "page",
            "content",
        ),
    }
)


def build_authorized_investigation_context(
    *, incident: Incident
) -> InvestigationContext:
    """Build the initial, investigation-scoped context from ORM records."""

    investigation = _load_root_investigation(incident)
    sources, hypotheses = _project_initial_context(investigation)
    return build_investigation_context(
        sources=sources,
        registered_hypotheses=hypotheses,
    )


def expand_with_equipment_history(
    context: InvestigationContext,
    *,
    incident: Incident,
    limit: int = 20,
) -> InvestigationContext:
    """Authorize only history returned by the existing equipment-history query."""

    root = _load_root_incident(incident)
    _assert_context_root(context, root)
    results = retrieval.search_equipment_history(
        organization=root.organization,
        equipment_id=root.equipment_id,
        exclude_incident_id=root.pk,
        limit=limit,
    )
    return extend_investigation_context(
        context,
        sources=_project_history_results(root, results),
    )


def expand_with_similar_incidents(
    context: InvestigationContext,
    *,
    incident: Incident,
    query: str = "",
    limit: int = 10,
) -> InvestigationContext:
    """Authorize only incidents returned by the current similarity retrieval."""

    root = _load_root_incident(incident)
    _assert_context_root(context, root)
    results = retrieval.search_similar_incidents(
        organization=root.organization,
        query=query,
        current_incident_id=root.pk,
        limit=limit,
    )
    return extend_investigation_context(
        context,
        sources=_project_history_results(root, results),
    )


def expand_with_documentation(
    context: InvestigationContext,
    *,
    incident: Incident,
    query: str,
    limit: int = 10,
) -> InvestigationContext:
    """Authorize only document sections returned for the root equipment."""

    root = _load_root_incident(incident)
    _assert_context_root(context, root)
    results = retrieval.search_documentation(
        organization=root.organization,
        equipment_id=root.equipment_id,
        query=query,
        limit=limit,
    )
    return extend_investigation_context(
        context,
        sources=_project_document_results(root, results),
    )


def _load_root_investigation(incident: Incident) -> Investigation:
    if not isinstance(incident, Incident) or not incident.pk:
        raise InvestigationRootNotFound(
            "an existing Incident is required to build investigation context"
        )
    try:
        investigation = (
            Investigation.objects.select_related(
                "incident__organization",
                "incident__equipment",
            )
            .prefetch_related(
                "facts",
                Prefetch(
                    "evidence",
                    queryset=Evidence.objects.select_related(
                        "source_document",
                        "source_incident",
                        "source_technician",
                    ),
                ),
                Prefetch(
                    "hypotheses",
                    queryset=Hypothesis.objects.prefetch_related(
                        Prefetch(
                            "evidence_links",
                            queryset=HypothesisEvidence.objects.select_related(
                                "evidence"
                            ),
                        )
                    ),
                ),
            )
            .get(incident_id=incident.pk)
        )
    except Investigation.DoesNotExist as error:
        raise InvestigationRootNotFound(
            "the Incident does not exist or has no Investigation"
        ) from error

    root = investigation.incident
    if root.organization_id != root.equipment.organization_id:
        raise InvestigationContextIntegrityError(
            "the root Incident and Equipment belong to different organizations"
        )
    return investigation


def _load_root_incident(incident: Incident) -> Incident:
    if not isinstance(incident, Incident) or not incident.pk:
        raise InvestigationRootNotFound(
            "an existing Incident is required to build investigation context"
        )
    try:
        root = Incident.objects.select_related(
            "organization", "equipment"
        ).get(pk=incident.pk, investigation__isnull=False)
    except Incident.DoesNotExist as error:
        raise InvestigationRootNotFound(
            "the Incident does not exist or has no Investigation"
        ) from error
    if root.organization_id != root.equipment.organization_id:
        raise InvestigationContextIntegrityError(
            "the root Incident and Equipment belong to different organizations"
        )
    return root


def _project_initial_context(
    investigation: Investigation,
) -> tuple[
    tuple[SourceCandidate, ...],
    tuple[RegisteredHypothesisCandidate, ...],
]:
    incident = investigation.incident
    equipment = incident.equipment
    sources = [
        _source(
            locator=_locator("incident", incident.pk),
            kind=SourceKind.CURRENT_INCIDENT,
            label="Ocorrência atual",
            description=incident.description,
            status=incident.status,
            occurred_at=incident.occurred_at.isoformat(),
        ),
        _source(
            locator=_locator("equipment", equipment.pk),
            kind=SourceKind.EQUIPMENT,
            label="Equipamento da ocorrência",
            name=equipment.name,
            code=equipment.code,
            manufacturer=equipment.manufacturer,
            model=equipment.model,
            location=equipment.location,
            status=equipment.status,
            description=equipment.description,
        ),
        _source(
            locator=_locator("investigation", investigation.pk),
            kind=SourceKind.INVESTIGATION,
            label="Investigação atual",
            status=investigation.status,
            started_at=investigation.started_at.isoformat(),
            finished_at=(
                investigation.finished_at.isoformat()
                if investigation.finished_at
                else None
            ),
        ),
    ]

    facts = tuple(investigation.facts.all())
    sources.extend(_project_fact(fact) for fact in facts)

    evidence_items = tuple(investigation.evidence.all())
    for evidence in evidence_items:
        _validate_evidence_provenance(evidence, investigation)
        sources.append(_project_evidence(evidence))

    authorized_evidence_ids = {item.pk for item in evidence_items}
    hypotheses = []
    for hypothesis in investigation.hypotheses.all():
        links = tuple(hypothesis.evidence_links.all())
        _validate_hypothesis_links(
            hypothesis,
            links,
            investigation,
            authorized_evidence_ids,
        )
        hypotheses.append(_project_hypothesis(hypothesis, links))

    return tuple(sources), tuple(hypotheses)


def _project_fact(fact: Fact) -> SourceCandidate:
    return _source(
        locator=_locator("fact", fact.pk),
        kind=SourceKind.FACT,
        label="Fato registrado",
        content=fact.content,
        source_type=fact.source_type,
    )


def _project_evidence(evidence: Evidence) -> SourceCandidate:
    return _source(
        locator=_locator("evidence", evidence.pk),
        kind=SourceKind.EVIDENCE,
        label="Evidência registrada",
        content=evidence.content,
        source_type=evidence.source_type,
        source_reference=evidence.source_reference,
    )


def _project_hypothesis(
    hypothesis: Hypothesis,
    links: Iterable[HypothesisEvidence],
) -> RegisteredHypothesisCandidate:
    supporting = []
    opposing = []
    for link in links:
        locator = _locator("evidence", link.evidence_id)
        if link.relation == HypothesisEvidence.Relation.SUPPORTS:
            supporting.append(locator)
        elif link.relation == HypothesisEvidence.Relation.CONTRADICTS:
            opposing.append(locator)
        else:
            raise InvestigationContextIntegrityError(
                "registered hypothesis has an unknown evidence relation"
            )
    return RegisteredHypothesisCandidate(
        internal_locator=_locator("hypothesis", hypothesis.pk),
        statement=hypothesis.description,
        observed_status=hypothesis.status,
        supporting_source_locators=tuple(supporting),
        opposing_source_locators=tuple(opposing),
    )


def _project_history_results(
    root: Incident,
    results: Iterable[retrieval.IncidentSummary],
) -> tuple[SourceCandidate, ...]:
    results = tuple(results)
    for result in results:
        if not isinstance(result, retrieval.IncidentSummary):
            raise InvestigationContextAuthorizationError(
                "history additions must come from incident retrieval"
            )
        if result.incident_id == root.pk:
            raise InvestigationContextAuthorizationError(
                "the current Incident cannot be reintroduced as history"
            )

    incident_ids = {result.incident_id for result in results}
    historical_by_id = {
        item.pk: item
        for item in Incident.objects.filter(
            pk__in=incident_ids,
            organization_id=root.organization_id,
        ).select_related("equipment")
    }
    if set(historical_by_id) != incident_ids:
        raise InvestigationContextAuthorizationError(
            "retrieved history is outside the root organization"
        )

    intervention_ids = {
        item.intervention_id
        for result in results
        for item in result.interventions
    }
    intervention_by_id = {
        item.pk: item
        for item in Intervention.objects.filter(
            pk__in=intervention_ids,
            incident_id__in=incident_ids,
        )
    }
    if set(intervention_by_id) != intervention_ids:
        raise InvestigationContextIntegrityError(
            "retrieved intervention does not belong to an authorized incident"
        )

    candidates = []
    seen_incidents = set()
    seen_interventions = set()
    for result in results:
        if result.incident_id in seen_incidents:
            continue
        seen_incidents.add(result.incident_id)
        historical = historical_by_id[result.incident_id]

        candidates.append(
            _source(
                locator=_locator("historical_incident", historical.pk),
                kind=SourceKind.HISTORICAL_INCIDENT,
                label="Ocorrência histórica recuperada",
                equipment_code=historical.equipment.code,
                description=historical.description,
                status=historical.status,
                occurred_at=historical.occurred_at.isoformat(),
            )
        )
        for item in result.interventions:
            if item.intervention_id in seen_interventions:
                continue
            seen_interventions.add(item.intervention_id)
            intervention = intervention_by_id[item.intervention_id]
            if intervention.incident_id != historical.pk:
                raise InvestigationContextIntegrityError(
                    "retrieved intervention does not belong to its incident"
                )
            candidates.append(
                _source(
                    locator=_locator("intervention", intervention.pk),
                    kind=SourceKind.INTERVENTION,
                    label="Intervenção histórica recuperada",
                    action_taken=intervention.action_taken,
                    confirmed_cause=intervention.confirmed_cause,
                    result=intervention.result,
                    created_at=intervention.created_at.isoformat(),
                )
            )
    return tuple(candidates)


def _project_document_results(
    root: Incident,
    results: Iterable[retrieval.DocumentSearchResult],
) -> tuple[SourceCandidate, ...]:
    results = tuple(results)
    for result in results:
        if not isinstance(result, retrieval.DocumentSearchResult):
            raise InvestigationContextAuthorizationError(
                "documentation additions must come from document retrieval"
            )

    identities = {(item.document_id, item.page) for item in results}
    if not identities:
        return ()
    identity_query = Q()
    for document_id, page in identities:
        identity_query |= Q(document_id=document_id, page_number=page)
    sections = (
        retrieval.DocumentSection.objects.select_related("document")
        .filter(
            identity_query,
            document__organization_id=root.organization_id,
            document__equipments=root.equipment_id,
        )
        .distinct()
    )
    sections_by_identity = {
        (section.document_id, section.page_number): section
        for section in sections
    }
    if set(sections_by_identity) != identities:
        raise InvestigationContextAuthorizationError(
            "retrieved document section is not authorized for the root equipment"
        )

    candidates = []
    seen = set()
    for result in results:
        identity = (result.document_id, result.page)
        if identity in seen:
            continue
        seen.add(identity)
        section = sections_by_identity[identity]
        candidates.append(
            _source(
                locator=_document_section_locator(
                    section.document_id, section.page_number
                ),
                kind=SourceKind.DOCUMENT_SECTION,
                label="Seção documental recuperada",
                document_title=section.document.title,
                page=section.page_number,
                content=section.content,
            )
        )
    return tuple(candidates)


def _validate_evidence_provenance(
    evidence: Evidence, investigation: Investigation
) -> None:
    organization_id = investigation.incident.organization_id
    if (
        evidence.source_document_id
        and evidence.source_document.organization_id != organization_id
    ):
        raise InvestigationContextIntegrityError(
            "evidence document belongs to another organization"
        )
    if (
        evidence.source_incident_id
        and evidence.source_incident.organization_id != organization_id
    ):
        raise InvestigationContextIntegrityError(
            "evidence incident belongs to another organization"
        )
    if (
        evidence.source_technician_id
        and evidence.source_technician.organization_id != organization_id
    ):
        raise InvestigationContextIntegrityError(
            "evidence technician belongs to another organization"
        )


def _validate_hypothesis_links(
    hypothesis: Hypothesis,
    links: Iterable[HypothesisEvidence],
    investigation: Investigation,
    authorized_evidence_ids: set[int],
) -> None:
    if hypothesis.investigation_id != investigation.pk:
        raise InvestigationContextAuthorizationError(
            "registered hypothesis belongs to another investigation"
        )
    for link in links:
        if link.evidence_id not in authorized_evidence_ids:
            raise InvestigationContextIntegrityError(
                "registered hypothesis points to unauthorized evidence"
            )
        if link.evidence.investigation_id != investigation.pk:
            raise InvestigationContextIntegrityError(
                "registered hypothesis evidence belongs to another investigation"
            )


def _assert_context_root(context: InvestigationContext, root: Incident) -> None:
    locator = _locator("incident", root.pk)
    matching = [
        source
        for source in context.sources
        if source.internal_locator == locator
        and source.kind is SourceKind.CURRENT_INCIDENT
    ]
    if len(matching) != 1:
        raise InvestigationContextAuthorizationError(
            "the context does not belong to the supplied root Incident"
        )


def _source(
    *,
    locator: str,
    kind: SourceKind,
    label: str,
    **fields: object,
) -> SourceCandidate:
    allowed = PROJECTION_POLICY.fields_by_kind.get(kind)
    if allowed is None or tuple(fields) != allowed:
        raise InvestigationContextIntegrityError(
            f"projection for {kind.value} does not match its allow-list"
        )
    return SourceCandidate(
        internal_locator=locator,
        kind=kind,
        authorized_content=json.dumps(
            fields,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        display_label=label,
    )


def _locator(model: str, pk: int) -> str:
    return f"{model}:{pk}"


def _document_section_locator(document_id: int, page: int) -> str:
    return f"document_section:{document_id}:{page}"
