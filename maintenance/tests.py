from io import StringIO
from tempfile import TemporaryDirectory

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import Organization, User
from assets.models import Equipment
from assets.models import Document
from knowledge.models import Evidence, Fact, Hypothesis, HypothesisEvidence

from .models import Incident, Intervention, Investigation
from .services import DomainError, record_resolution, start_investigation


class MaintenanceServiceTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Indústria Alfa")
        self.other_organization = Organization.objects.create(name="Indústria Beta")
        self.technician = User.objects.create_user(
            username="tecnico",
            password="test-password",
            organization=self.organization,
            role=User.Role.TECHNICIAN,
        )
        self.other_technician = User.objects.create_user(
            username="outro-tecnico",
            password="test-password",
            organization=self.other_organization,
            role=User.Role.TECHNICIAN,
        )
        self.equipment = Equipment.objects.create(
            organization=self.organization,
            name="Compressor",
            code="C-01",
        )
        self.incident = Incident.objects.create(
            organization=self.organization,
            equipment=self.equipment,
            description="Pressão abaixo do esperado.",
            reported_by=self.technician,
        )

    def test_start_investigation_changes_incident_status(self):
        investigation = start_investigation(
            incident=self.incident, technician=self.technician
        )

        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.UNDER_INVESTIGATION)
        self.assertEqual(investigation.status, Investigation.Status.ACTIVE)

    def test_incident_has_only_one_investigation(self):
        start_investigation(incident=self.incident, technician=self.technician)

        with self.assertRaises(DomainError):
            start_investigation(incident=self.incident, technician=self.technician)

        self.assertEqual(Investigation.objects.filter(incident=self.incident).count(), 1)

    def test_incident_rejects_invalid_status_transition(self):
        with self.assertRaises(ValidationError):
            self.incident.transition_to(Incident.Status.RESOLVED)

    def test_other_organization_cannot_start_investigation(self):
        with self.assertRaises(DomainError):
            start_investigation(
                incident=self.incident, technician=self.other_technician
            )

        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.OPEN)

    def test_record_resolution_creates_intervention_and_finishes_flow(self):
        investigation = start_investigation(
            incident=self.incident, technician=self.technician
        )

        intervention = record_resolution(
            incident=self.incident,
            technician=self.technician,
            action_taken="Substituição da vedação.",
            confirmed_cause="Vedação rompida.",
            result="Pressão normalizada.",
        )

        self.incident.refresh_from_db()
        investigation.refresh_from_db()
        self.assertEqual(self.incident.status, Incident.Status.RESOLVED)
        self.assertEqual(investigation.status, Investigation.Status.FINISHED)
        self.assertIsNotNone(investigation.finished_at)
        self.assertEqual(intervention.confirmed_cause, "Vedação rompida.")
        self.assertEqual(Intervention.objects.filter(incident=self.incident).count(), 1)


class DemoScenarioTests(TestCase):
    def test_seed_demo_is_complete_and_idempotent(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            output = StringIO()
            call_command("seed_demo", password="local-test-password", stdout=output)
            call_command("seed_demo", password="local-test-password", stdout=output)

            organization = Organization.objects.get(name="FaultTrace Demo")
            equipment = Equipment.objects.get(organization=organization, code="C-04")
            current = Incident.objects.get(
                organization=organization,
                description__startswith="Equipamento desliga após aproximadamente",
            )
            investigation = Investigation.objects.get(incident=current)
            document = Document.objects.get(
                organization=organization,
                title="Manual AX-200",
            )

            self.assertTrue(document.file.storage.exists(document.file.name))
            self.assertTrue(
                document.sections.filter(page_number=34, content__icontains="E07").exists()
            )
            self.assertEqual(
                Incident.objects.filter(organization=organization).count(), 2
            )
            self.assertEqual(Investigation.objects.filter(incident=current).count(), 1)
            self.assertEqual(Fact.objects.filter(investigation=investigation).count(), 1)
            self.assertEqual(
                Evidence.objects.filter(investigation=investigation).count(), 2
            )
            self.assertEqual(
                Hypothesis.objects.filter(investigation=investigation).count(), 1
            )
            self.assertEqual(
                HypothesisEvidence.objects.filter(
                    hypothesis__investigation=investigation
                ).count(),
                2,
            )
            self.assertEqual(current.interventions.count(), 0)
            self.assertIn("Cenário demo preparado com sucesso", output.getvalue())
