from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import Organization, User
from assets.models import Equipment

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

