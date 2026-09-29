from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from accounts.models import Organization, User
from assets.models import Document, Equipment
from maintenance.models import Incident, Intervention, Investigation

from .models import DocumentSection, Evidence, Fact, Hypothesis, HypothesisEvidence
from .retrieval import (
    RetrievalNotFound,
    get_equipment_context,
    get_incident_details,
    index_document,
    search_documentation,
    search_equipment_history,
    search_similar_incidents,
)
from .services import DomainError, register_evidence, relate_evidence


class KnowledgeLayerTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Indústria Alfa")
        self.other_organization = Organization.objects.create(name="Indústria Beta")
        self.technician = User.objects.create_user(
            username="tecnico-alfa",
            password="test-password",
            organization=self.organization,
            role=User.Role.TECHNICIAN,
        )
        self.other_technician = User.objects.create_user(
            username="tecnico-beta",
            password="test-password",
            organization=self.other_organization,
            role=User.Role.TECHNICIAN,
        )
        self.equipment = Equipment.objects.create(
            organization=self.organization,
            name="Compressor",
            code="C-04",
        )
        self.other_equipment = Equipment.objects.create(
            organization=self.other_organization,
            name="Bomba",
            code="B-01",
        )
        self.incident = Incident.objects.create(
            organization=self.organization,
            equipment=self.equipment,
            description="Desliga após aquecimento.",
            reported_by=self.technician,
        )
        self.past_incident = Incident.objects.create(
            organization=self.organization,
            equipment=self.equipment,
            description="Ocorrência anterior com problema X.",
            reported_by=self.technician,
        )
        self.other_incident = Incident.objects.create(
            organization=self.other_organization,
            equipment=self.other_equipment,
            description="Falha de outra empresa.",
            reported_by=self.other_technician,
        )
        self.investigation = Investigation.objects.create(
            incident=self.incident,
            technician=self.technician,
        )
        self.other_investigation = Investigation.objects.create(
            incident=self.other_incident,
            technician=self.other_technician,
        )
        self.document = Document.objects.create(
            organization=self.organization,
            title="Manual AX-200",
            file="documents/manual-ax-200.pdf",
        )
        self.other_document = Document.objects.create(
            organization=self.other_organization,
            title="Manual externo",
            file="documents/manual-externo.pdf",
        )

    def test_evidence_requires_coherent_provenance(self):
        source_types = (
            Evidence.SourceType.DOCUMENT,
            Evidence.SourceType.PAST_INCIDENT,
            Evidence.SourceType.TECHNICIAN,
        )
        for source_type in source_types:
            with self.subTest(source_type=source_type), self.assertRaises(DomainError):
                register_evidence(
                    investigation=self.investigation,
                    created_by=self.technician,
                    content="Informação sem a fonte exigida.",
                    source_type=source_type,
                )

        with self.assertRaises(DomainError):
            register_evidence(
                investigation=self.investigation,
                created_by=self.technician,
                content="Fontes incompatíveis.",
                source_type=Evidence.SourceType.DOCUMENT,
                source_document=self.document,
                source_technician=self.technician,
            )

    def test_document_from_other_organization_is_rejected(self):
        with self.assertRaises(DomainError):
            register_evidence(
                investigation=self.investigation,
                created_by=self.technician,
                content="Conteúdo de documento externo.",
                source_type=Evidence.SourceType.DOCUMENT,
                source_document=self.other_document,
            )

    def test_incident_from_other_organization_is_rejected(self):
        with self.assertRaises(DomainError):
            register_evidence(
                investigation=self.investigation,
                created_by=self.technician,
                content="Histórico externo.",
                source_type=Evidence.SourceType.PAST_INCIDENT,
                source_incident=self.other_incident,
            )

    def test_evidence_cannot_be_related_to_another_investigation(self):
        evidence = register_evidence(
            investigation=self.investigation,
            created_by=self.technician,
            content="Manual associa E07 à proteção térmica.",
            source_type=Evidence.SourceType.DOCUMENT,
            source_document=self.document,
        )
        other_hypothesis = Hypothesis.objects.create(
            investigation=self.other_investigation,
            description="Hipótese de outra investigação.",
            created_by=self.other_technician,
        )

        with self.assertRaises(DomainError):
            relate_evidence(
                hypothesis=other_hypothesis,
                evidence=evidence,
                relation=HypothesisEvidence.Relation.SUPPORTS,
                user=self.other_technician,
            )

        invalid_link = HypothesisEvidence(
            hypothesis=other_hypothesis,
            evidence=evidence,
            relation=HypothesisEvidence.Relation.CONTRADICTS,
        )
        with self.assertRaises(ValidationError):
            invalid_link.full_clean()

    def test_supports_and_contradicts_relations_work_without_duplicates(self):
        hypothesis = Hypothesis.objects.create(
            investigation=self.investigation,
            description="Pode existir uma condição térmica anormal.",
            created_by=self.technician,
        )
        historical = register_evidence(
            investigation=self.investigation,
            created_by=self.technician,
            content="Ocorrências anteriores tiveram problema X.",
            source_type=Evidence.SourceType.PAST_INCIDENT,
            source_incident=self.past_incident,
        )
        current = register_evidence(
            investigation=self.investigation,
            created_by=self.technician,
            content="Técnico verificou X e não encontrou o problema.",
            source_type=Evidence.SourceType.TECHNICIAN,
            source_technician=self.technician,
        )

        relate_evidence(
            hypothesis=hypothesis,
            evidence=historical,
            relation=HypothesisEvidence.Relation.SUPPORTS,
            user=self.technician,
        )
        relate_evidence(
            hypothesis=hypothesis,
            evidence=current,
            relation=HypothesisEvidence.Relation.CONTRADICTS,
            user=self.technician,
        )

        self.assertTrue(
            hypothesis.evidence_links.filter(
                evidence=historical,
                relation=HypothesisEvidence.Relation.SUPPORTS,
            ).exists()
        )
        self.assertTrue(
            hypothesis.evidence_links.filter(
                evidence=current,
                relation=HypothesisEvidence.Relation.CONTRADICTS,
            ).exists()
        )
        with self.assertRaises(DomainError):
            relate_evidence(
                hypothesis=hypothesis,
                evidence=historical,
                relation=HypothesisEvidence.Relation.SUPPORTS,
                user=self.technician,
            )

    def test_cross_organization_knowledge_access_is_blocked(self):
        other_hypothesis = Hypothesis.objects.create(
            investigation=self.other_investigation,
            description="Conhecimento sigiloso da Empresa Beta.",
            created_by=self.other_technician,
        )
        self.client.force_login(self.technician)

        protected_requests = (
            ("get", reverse("hypothesis_manage", args=[other_hypothesis.pk]), {}),
            (
                "post",
                reverse("fact_create", args=[self.other_incident.pk]),
                {"content": "Tentativa cruzada", "source_type": "TECHNICIAN"},
            ),
            (
                "post",
                reverse("evidence_create", args=[self.other_incident.pk]),
                {"content": "Tentativa cruzada", "source_type": "TECHNICIAN"},
            ),
        )
        for method, url, data in protected_requests:
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, data)
                self.assertEqual(response.status_code, 404)

    def test_experimental_case_03_through_web(self):
        self.client.force_login(self.technician)
        response = self.client.post(
            reverse("hypothesis_create", args=[self.incident.pk]),
            {"description": "Problema X pode ter ocorrido novamente."},
        )
        hypothesis = Hypothesis.objects.get(investigation=self.investigation)
        self.assertRedirects(response, reverse("hypothesis_manage", args=[hypothesis.pk]))

        evidence_payloads = (
            {
                "content": "Ocorrências anteriores tiveram problema X.",
                "source_type": Evidence.SourceType.PAST_INCIDENT,
                "source_incident": self.past_incident.pk,
                "source_reference": "Histórico do C-04",
            },
            {
                "content": "Técnico verificou X e não encontrou o problema.",
                "source_type": Evidence.SourceType.TECHNICIAN,
                "source_technician": self.technician.pk,
                "source_reference": "Verificação no equipamento atual",
            },
        )
        for payload in evidence_payloads:
            response = self.client.post(
                reverse("evidence_create", args=[self.incident.pk]), payload
            )
            self.assertRedirects(response, reverse("incident_detail", args=[self.incident.pk]))

        historical, current = self.investigation.evidence.order_by("pk")
        for evidence, relation in (
            (historical, HypothesisEvidence.Relation.SUPPORTS),
            (current, HypothesisEvidence.Relation.CONTRADICTS),
        ):
            response = self.client.post(
                reverse("hypothesis_evidence_create", args=[hypothesis.pk]),
                {"evidence": evidence.pk, "relation": relation},
            )
            self.assertRedirects(
                response, reverse("hypothesis_manage", args=[hypothesis.pk])
            )

        response = self.client.post(
            reverse("hypothesis_status_update", args=[hypothesis.pk]),
            {"status": Hypothesis.Status.WEAKENED},
        )
        self.assertRedirects(response, reverse("hypothesis_manage", args=[hypothesis.pk]))

        response = self.client.get(reverse("hypothesis_manage", args=[hypothesis.pk]))
        self.assertContains(response, "Sustentam")
        self.assertContains(response, "Contradizem")
        self.assertContains(response, f"Ocorrência #{self.past_incident.pk}")
        self.assertContains(response, "Histórico do C-04")
        self.assertContains(response, "Verificação no equipamento atual")
        self.assertContains(response, "Enfraquecida")


def _pdf_bytes(text=None):
    output = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_reference = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        stream = DecodedStreamObject()
        safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(
            f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode("latin-1")
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(output)
    return output.getvalue()


class KnowledgeRetrievalTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Recuperação Alfa")
        self.other_organization = Organization.objects.create(name="Recuperação Beta")
        self.technician = User.objects.create_user(
            username="retrieval-a",
            password="test-password",
            organization=self.organization,
            role=User.Role.TECHNICIAN,
        )
        self.other_technician = User.objects.create_user(
            username="retrieval-b",
            password="test-password",
            organization=self.other_organization,
            role=User.Role.TECHNICIAN,
        )
        self.equipment = Equipment.objects.create(
            organization=self.organization,
            name="Compressor C-04",
            code="C-04",
            manufacturer="Atlas",
            model="AX-200",
            location="Linha 1",
        )
        self.other_equipment = Equipment.objects.create(
            organization=self.other_organization,
            name="Compressor externo",
            code="C-04",
            manufacturer="Atlas",
            model="AX-200",
        )
        self.current_incident = Incident.objects.create(
            organization=self.organization,
            equipment=self.equipment,
            description="Equipamento desliga quando aquece.",
            reported_by=self.technician,
        )
        self.past_incident = Incident.objects.create(
            organization=self.organization,
            equipment=self.equipment,
            description="Desligamento após aquecimento.",
            status=Incident.Status.RESOLVED,
            reported_by=self.technician,
        )
        self.intervention = Intervention.objects.create(
            incident=self.past_incident,
            technician=self.technician,
            action_taken="Limpeza da ventilação.",
            confirmed_cause="Falha de ventilação.",
            result="Operação normalizada.",
        )
        self.other_incident = Incident.objects.create(
            organization=self.other_organization,
            equipment=self.other_equipment,
            description="Desligamento por falha de ventilação sigilosa.",
            reported_by=self.other_technician,
        )
        self.investigation = Investigation.objects.create(
            incident=self.current_incident,
            technician=self.technician,
        )

    def test_equipment_context_and_history_are_organization_scoped(self):
        document = Document.objects.create(
            organization=self.organization,
            title="Manual AX-200",
            file="documents/manual.pdf",
        )
        document.equipments.add(self.equipment)

        context = get_equipment_context(
            organization=self.organization,
            equipment_id=self.equipment.pk,
        )
        history = search_equipment_history(
            organization=self.organization,
            equipment_id=self.equipment.pk,
            exclude_incident_id=self.current_incident.pk,
        )

        self.assertEqual(context.code, "C-04")
        self.assertEqual(context.documents[0].title, "Manual AX-200")
        self.assertEqual([item.incident_id for item in history], [self.past_incident.pk])
        self.assertEqual(
            history[0].interventions[0].confirmed_cause,
            "Falha de ventilação.",
        )
        self.assertIn(f"Ocorrência #{self.past_incident.pk}", history[0].reference)
        with self.assertRaises(RetrievalNotFound):
            get_equipment_context(
                organization=self.organization,
                equipment_id=self.other_equipment.pk,
            )

    def test_incident_details_preserve_evidence_provenance(self):
        Fact.objects.create(
            investigation=self.investigation,
            content="Código atual E07.",
            source_type=Fact.SourceType.TECHNICIAN,
            created_by=self.technician,
        )
        evidence = Evidence.objects.create(
            investigation=self.investigation,
            content="Histórico registra falha de ventilação.",
            source_type=Evidence.SourceType.PAST_INCIDENT,
            source_incident=self.past_incident,
            source_reference="Caso anterior do C-04",
            created_by=self.technician,
        )
        hypothesis = Hypothesis.objects.create(
            investigation=self.investigation,
            description="A falha de ventilação pode ter voltado.",
            created_by=self.technician,
        )
        HypothesisEvidence.objects.create(
            hypothesis=hypothesis,
            evidence=evidence,
            relation=HypothesisEvidence.Relation.SUPPORTS,
        )

        details = get_incident_details(
            organization=self.organization,
            incident_id=self.current_incident.pk,
        )

        self.assertEqual(details.facts[0]["content"], "Código atual E07.")
        self.assertEqual(
            details.evidence[0]["reference"],
            f"Ocorrência #{self.past_incident.pk} — Caso anterior do C-04",
        )
        self.assertEqual(
            details.hypotheses[0]["supports"][0]["reference"],
            details.evidence[0]["reference"],
        )

    def test_similar_incidents_do_not_cross_organization(self):
        results = search_similar_incidents(
            organization=self.organization,
            query="falha ventilação",
            current_incident_id=self.current_incident.pk,
        )

        self.assertEqual([item.incident_id for item in results], [self.past_incident.pk])
        self.assertNotIn(self.other_incident.pk, [item.incident_id for item in results])

    def test_pdf_indexing_and_document_search_preserve_page_reference(self):
        document = Document.objects.create(
            organization=self.organization,
            title="Manual AX-200",
            file=SimpleUploadedFile(
                "manual.pdf",
                _pdf_bytes("E07 esta associado a protecao termica."),
                content_type="application/pdf",
            ),
        )
        document.equipments.add(self.equipment)
        other_document = Document.objects.create(
            organization=self.other_organization,
            title="Manual sigiloso",
            file="documents/other.pdf",
        )
        other_document.equipments.add(self.other_equipment)
        DocumentSection.objects.create(
            document=other_document,
            page_number=1,
            content="E07 informação de outra empresa.",
        )

        indexing = index_document(document)
        results = search_documentation(
            organization=self.organization,
            equipment_id=self.equipment.pk,
            query="E07",
        )

        self.assertEqual(indexing.status, "INDEXED")
        self.assertEqual(indexing.pages_indexed, 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document_id, document.pk)
        self.assertEqual(results[0].page, 1)
        self.assertEqual(results[0].reference, "Manual AX-200 — página 1")

    def test_pdf_without_text_and_invalid_pdf_do_not_break_indexing(self):
        blank_document = Document.objects.create(
            organization=self.organization,
            title="PDF escaneado",
            file=SimpleUploadedFile(
                "blank.pdf",
                _pdf_bytes(),
                content_type="application/pdf",
            ),
        )
        invalid_document = Document.objects.create(
            organization=self.organization,
            title="PDF inválido",
            file=SimpleUploadedFile(
                "invalid.pdf",
                b"not a pdf",
                content_type="application/pdf",
            ),
        )

        blank_result = index_document(blank_document)
        invalid_result = index_document(invalid_document)

        self.assertEqual(blank_result.status, "NO_TEXT")
        self.assertEqual(blank_document.sections.count(), 0)
        self.assertEqual(invalid_result.status, "FAILED")
        self.assertTrue(invalid_result.error)

    def test_retrieval_page_uses_authorized_context(self):
        self.client.force_login(self.technician)
        response = self.client.get(
            reverse("related_knowledge", args=[self.current_incident.pk]),
            {"q": "ventilação"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Conhecimento relacionado")
        self.assertContains(response, "Falha de ventilação.")
        self.assertNotContains(response, "sigilosa")
