import re
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from pypdf import PdfReader

from assets.models import Document, Equipment
from maintenance.models import Incident

from .models import DocumentSection, HypothesisEvidence


class RetrievalNotFound(Exception):
    pass


@dataclass(frozen=True)
class DocumentSummary:
    document_id: int
    title: str
    description: str


@dataclass(frozen=True)
class EquipmentContext:
    equipment_id: int
    name: str
    code: str
    manufacturer: str
    model: str
    location: str
    status: str
    description: str
    documents: tuple[DocumentSummary, ...]


@dataclass(frozen=True)
class InterventionSummary:
    intervention_id: int
    action_taken: str
    confirmed_cause: str
    result: str
    created_at: str


@dataclass(frozen=True)
class IncidentSummary:
    incident_id: int
    equipment_id: int
    equipment_code: str
    description: str
    status: str
    occurred_at: str
    interventions: tuple[InterventionSummary, ...]
    reference: str


@dataclass(frozen=True)
class IncidentDetails:
    incident: IncidentSummary
    investigation: dict | None
    facts: tuple[dict, ...]
    evidence: tuple[dict, ...]
    hypotheses: tuple[dict, ...]


@dataclass(frozen=True)
class DocumentSearchResult:
    document_id: int
    document_title: str
    page: int
    content: str
    reference: str


@dataclass(frozen=True)
class IndexingResult:
    status: str
    pages_indexed: int
    error: str = ""


STOPWORDS = {
    "a",
    "ao",
    "as",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "esta",
    "este",
    "o",
    "os",
    "para",
    "por",
    "quando",
    "que",
    "um",
    "uma",
}


def _iso(value: datetime) -> str:
    return value.isoformat()


def _get_equipment(*, organization, equipment_id):
    try:
        return Equipment.objects.get(organization=organization, pk=equipment_id)
    except Equipment.DoesNotExist as error:
        raise RetrievalNotFound("Equipamento não encontrado nesta organização.") from error


def _get_incident(*, organization, incident_id):
    try:
        return Incident.objects.select_related("equipment").get(
            organization=organization,
            pk=incident_id,
        )
    except Incident.DoesNotExist as error:
        raise RetrievalNotFound("Ocorrência não encontrada nesta organização.") from error


def _intervention_summary(intervention):
    return InterventionSummary(
        intervention_id=intervention.pk,
        action_taken=intervention.action_taken,
        confirmed_cause=intervention.confirmed_cause,
        result=intervention.result,
        created_at=_iso(intervention.created_at),
    )


def _incident_summary(incident):
    return IncidentSummary(
        incident_id=incident.pk,
        equipment_id=incident.equipment_id,
        equipment_code=incident.equipment.code,
        description=incident.description,
        status=incident.status,
        occurred_at=_iso(incident.occurred_at),
        interventions=tuple(
            _intervention_summary(intervention)
            for intervention in incident.interventions.all()
        ),
        reference=f"Ocorrência #{incident.pk} — {incident.equipment.code}",
    )


def get_equipment_context(*, organization, equipment_id):
    equipment = _get_equipment(
        organization=organization,
        equipment_id=equipment_id,
    )
    documents = equipment.documents.filter(organization=organization)
    return EquipmentContext(
        equipment_id=equipment.pk,
        name=equipment.name,
        code=equipment.code,
        manufacturer=equipment.manufacturer,
        model=equipment.model,
        location=equipment.location,
        status=equipment.status,
        description=equipment.description,
        documents=tuple(
            DocumentSummary(
                document_id=document.pk,
                title=document.title,
                description=document.description,
            )
            for document in documents
        ),
    )


def search_equipment_history(
    *, organization, equipment_id, exclude_incident_id=None, limit=20
):
    equipment = _get_equipment(
        organization=organization,
        equipment_id=equipment_id,
    )
    incidents = (
        Incident.objects.filter(
            organization=organization,
            equipment=equipment,
        )
        .select_related("equipment")
        .prefetch_related("interventions")
    )
    if exclude_incident_id is not None:
        incidents = incidents.exclude(pk=exclude_incident_id)
    return tuple(_incident_summary(incident) for incident in incidents[:limit])


def get_incident_details(*, organization, incident_id):
    incident = _get_incident(organization=organization, incident_id=incident_id)
    incident = (
        Incident.objects.filter(pk=incident.pk)
        .select_related("equipment", "investigation__technician")
        .prefetch_related(
            "interventions",
            "investigation__facts__created_by",
            "investigation__evidence__source_document",
            "investigation__evidence__source_incident",
            "investigation__evidence__source_technician",
            "investigation__hypotheses__evidence_links__evidence__source_document",
            "investigation__hypotheses__evidence_links__evidence__source_incident",
            "investigation__hypotheses__evidence_links__evidence__source_technician",
        )
        .get()
    )
    summary = _incident_summary(incident)
    investigation = getattr(incident, "investigation", None)
    if not investigation:
        return IncidentDetails(summary, None, (), (), ())

    evidence = tuple(
        {
            "evidence_id": item.pk,
            "content": item.content,
            "source_type": item.source_type,
            "reference": item.source_label,
        }
        for item in investigation.evidence.all()
    )
    hypotheses = []
    for hypothesis in investigation.hypotheses.all():
        links = list(hypothesis.evidence_links.all())
        hypotheses.append(
            {
                "hypothesis_id": hypothesis.pk,
                "description": hypothesis.description,
                "status": hypothesis.status,
                "supports": tuple(
                    {
                        "evidence_id": link.evidence_id,
                        "content": link.evidence.content,
                        "reference": link.evidence.source_label,
                    }
                    for link in links
                    if link.relation == HypothesisEvidence.Relation.SUPPORTS
                ),
                "contradicts": tuple(
                    {
                        "evidence_id": link.evidence_id,
                        "content": link.evidence.content,
                        "reference": link.evidence.source_label,
                    }
                    for link in links
                    if link.relation == HypothesisEvidence.Relation.CONTRADICTS
                ),
            }
        )

    return IncidentDetails(
        incident=summary,
        investigation={
            "investigation_id": investigation.pk,
            "status": investigation.status,
            "technician": str(investigation.technician),
            "started_at": _iso(investigation.started_at),
            "finished_at": (
                _iso(investigation.finished_at) if investigation.finished_at else None
            ),
        },
        facts=tuple(
            {
                "fact_id": fact.pk,
                "content": fact.content,
                "source_type": fact.source_type,
                "created_by": str(fact.created_by) if fact.created_by else None,
            }
            for fact in investigation.facts.all()
        ),
        evidence=evidence,
        hypotheses=tuple(hypotheses),
    )


def _search_terms(query):
    return tuple(
        term
        for term in re.findall(r"[\w-]+", query.lower())
        if len(term) >= 3 and term not in STOPWORDS
    )


def _text_query(terms, fields):
    query = Q()
    for term in terms:
        for field in fields:
            query |= Q(**{f"{field}__icontains": term})
    return query


def search_similar_incidents(
    *, organization, query="", current_incident_id=None, limit=10
):
    current_incident = None
    if current_incident_id is not None:
        current_incident = _get_incident(
            organization=organization,
            incident_id=current_incident_id,
        )

    incidents = Incident.objects.filter(organization=organization)
    if current_incident:
        incidents = incidents.exclude(pk=current_incident.pk)

    terms = _search_terms(query)
    if terms:
        incidents = incidents.filter(
            _text_query(
                terms,
                (
                    "description",
                    "equipment__name",
                    "equipment__code",
                    "equipment__manufacturer",
                    "equipment__model",
                    "interventions__confirmed_cause",
                    "interventions__action_taken",
                    "interventions__result",
                ),
            )
        )
    elif current_incident:
        related = Q(equipment=current_incident.equipment)
        equipment = current_incident.equipment
        if equipment.manufacturer and equipment.model:
            related |= Q(
                equipment__manufacturer__iexact=equipment.manufacturer,
                equipment__model__iexact=equipment.model,
            )
        incidents = incidents.filter(related)
    else:
        return ()

    if current_incident:
        incidents = incidents.annotate(
            equipment_priority=Case(
                When(equipment=current_incident.equipment, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by("equipment_priority", "-occurred_at")
    else:
        incidents = incidents.order_by("-occurred_at")

    incidents = incidents.select_related("equipment").prefetch_related(
        "interventions"
    )
    return tuple(
        _incident_summary(incident) for incident in incidents.distinct()[:limit]
    )


def search_documentation(*, organization, equipment_id, query, limit=10):
    equipment = _get_equipment(
        organization=organization,
        equipment_id=equipment_id,
    )
    terms = _search_terms(query)
    if not terms:
        return ()

    sections = (
        DocumentSection.objects.filter(
            document__organization=organization,
            document__equipments=equipment,
        )
        .filter(_text_query(terms, ("content",)))
        .select_related("document")
        .distinct()
        .order_by("document__title", "page_number")[:limit]
    )
    return tuple(
        DocumentSearchResult(
            document_id=section.document_id,
            document_title=section.document.title,
            page=section.page_number,
            content=section.content,
            reference=section.reference,
        )
        for section in sections
    )


def _normalize_page_text(text):
    return " ".join(text.split())


def index_document(document):
    if document.file.name.rsplit(".", 1)[-1].lower() != "pdf":
        return IndexingResult(status="UNSUPPORTED", pages_indexed=0)

    try:
        with document.file.open("rb") as stream:
            reader = PdfReader(stream)
            pages = [
                (page_number, _normalize_page_text(page.extract_text() or ""))
                for page_number, page in enumerate(reader.pages, start=1)
            ]
    except Exception as error:
        return IndexingResult(
            status="FAILED",
            pages_indexed=0,
            error=str(error)[:500],
        )

    text_pages = [(page, content) for page, content in pages if content]
    with transaction.atomic():
        document.sections.all().delete()
        DocumentSection.objects.bulk_create(
            DocumentSection(
                document=document,
                page_number=page_number,
                content=content,
            )
            for page_number, content in text_pages
        )

    if not text_pages:
        return IndexingResult(status="NO_TEXT", pages_indexed=0)
    return IndexingResult(status="INDEXED", pages_indexed=len(text_pages))
