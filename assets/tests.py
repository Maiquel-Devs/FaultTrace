from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Organization, User
from maintenance.models import Incident
from knowledge.models import DocumentSection

from .models import Document, Equipment


@override_settings(MEDIA_ROOT="/tmp/faulttrace-test-media")
class OrganizationIsolationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Empresa A")
        self.other_organization = Organization.objects.create(name="Empresa B")
        self.user = User.objects.create_user(
            username="admin-a",
            password="test-password",
            organization=self.organization,
            role=User.Role.ADMIN,
        )
        self.other_user = User.objects.create_user(
            username="admin-b",
            password="test-password",
            organization=self.other_organization,
            role=User.Role.ADMIN,
        )
        self.other_equipment = Equipment.objects.create(
            organization=self.other_organization,
            name="Bomba B",
            code="B-01",
        )
        self.other_incident = Incident.objects.create(
            organization=self.other_organization,
            equipment=self.other_equipment,
            description="Falha externa.",
            reported_by=self.other_user,
        )
        self.other_document = Document.objects.create(
            organization=self.other_organization,
            title="Manual B",
            file=SimpleUploadedFile("manual.txt", b"conteudo tecnico"),
        )
        self.client.force_login(self.user)

    def test_user_cannot_view_other_organization_records(self):
        protected_urls = [
            reverse("equipment_detail", args=[self.other_equipment.pk]),
            reverse("document_detail", args=[self.other_document.pk]),
            reverse("document_download", args=[self.other_document.pk]),
            reverse("incident_detail", args=[self.other_incident.pk]),
        ]

        for url in protected_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_user_cannot_create_incident_for_other_organization_equipment(self):
        response = self.client.post(
            reverse("incident_create"),
            {
                "equipment": self.other_equipment.pk,
                "description": "Tentativa cruzada.",
                "occurred_at": "2026-09-28T10:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Incident.objects.count(), 1)

    def test_missing_document_file_is_reported_without_losing_indexed_content(self):
        document = Document.objects.create(
            organization=self.organization,
            title="Manual sem arquivo físico",
            file="documents/missing.pdf",
        )
        DocumentSection.objects.create(
            document=document,
            page_number=34,
            content="Conteúdo técnico ainda pesquisável.",
        )

        detail = self.client.get(reverse("document_detail", args=[document.pk]))
        download = self.client.get(
            reverse("document_download", args=[document.pk]),
            follow=True,
        )

        self.assertContains(detail, "Arquivo físico indisponível")
        self.assertNotContains(detail, "Abrir arquivo")
        self.assertContains(download, "conteúdo já indexado continua pesquisável")
        self.assertEqual(document.sections.count(), 1)


class OperationalAuthenticationTests(TestCase):
    def test_operational_pages_require_authentication(self):
        urls = [
            reverse("home"),
            reverse("equipment_list"),
            reverse("document_list"),
            reverse("incident_list"),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response.url)
