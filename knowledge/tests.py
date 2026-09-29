from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import Organization, User
from assets.models import Document, Equipment
from maintenance.models import Incident, Investigation

from .models import Evidence, Hypothesis, HypothesisEvidence
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
