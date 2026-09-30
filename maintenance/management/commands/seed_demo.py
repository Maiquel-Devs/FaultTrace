from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from accounts.models import Organization, User
from assets.models import Document, Equipment
from knowledge.models import Evidence, Fact, Hypothesis, HypothesisEvidence
from knowledge.retrieval import index_document
from maintenance.models import Incident, Intervention, Investigation


ORGANIZATION_NAME = "FaultTrace Demo"
ADMIN_USERNAME = "demo_admin"
TECHNICIAN_USERNAME = "demo_technician"
DOCUMENT_TITLE = "Manual AX-200"
DOCUMENT_FILE = "documents/demo/manual-ax-200-demo.pdf"
CURRENT_DESCRIPTION = (
    "Equipamento desliga após aproximadamente 20 minutos de operação e apresenta código E07."
)
HISTORY_DESCRIPTION = (
    "Ocorrência anterior: desligamento semelhante após aquecimento do compressor."
)
MANUAL_CONTENT = (
    "O código E07 indica atuação da proteção térmica. Verificar temperatura do motor, "
    "corrente elétrica, sensor térmico e fluxo de ventilação antes de determinar a causa "
    "do desligamento."
)


def _demo_pdf():
    output = BytesIO()
    writer = PdfWriter()
    for _ in range(34):
        writer.add_blank_page(width=612, height=792)
    page = writer.pages[33]
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
    safe_text = MANUAL_CONTENT.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(
        f"BT /F1 10 Tf 50 720 Td ({safe_text}) Tj ET".encode("latin-1")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(output)
    return output.getvalue()


class Command(BaseCommand):
    help = "Cria ou atualiza o cenário determinístico de demonstração do FaultTrace."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            required=True,
            help="Senha local do usuário demo_admin. Não é salva em código ou exibida.",
        )
        parser.add_argument(
            "--organization",
            default=ORGANIZATION_NAME,
            help=f"Nome da organização demo (padrão: {ORGANIZATION_NAME}).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        organization, _ = Organization.objects.get_or_create(
            name=options["organization"]
        )
        admin = self._user(
            username=ADMIN_USERNAME,
            organization=organization,
            role=User.Role.ADMIN,
        )
        admin.set_password(options["password"])
        admin.save(update_fields=["password"])
        technician = self._user(
            username=TECHNICIAN_USERNAME,
            organization=organization,
            role=User.Role.TECHNICIAN,
        )
        if not technician.has_usable_password():
            pass
        else:
            technician.set_unusable_password()
            technician.save(update_fields=["password"])

        equipment, _ = Equipment.objects.update_or_create(
            organization=organization,
            code="C-04",
            defaults={
                "name": "Compressor C-04",
                "manufacturer": "FaultTrace Industrial",
                "model": "AX-200",
                "location": "Linha de demonstração",
                "description": "Compressor usado no cenário demonstrativo do FaultTrace.",
                "status": Equipment.Status.ACTIVE,
            },
        )
        current = self._incident(
            organization=organization,
            equipment=equipment,
            description=CURRENT_DESCRIPTION,
            reported_by=technician,
            status=Incident.Status.UNDER_INVESTIGATION,
        )
        investigation, _ = Investigation.objects.get_or_create(
            incident=current,
            defaults={"technician": technician},
        )
        if investigation.status != Investigation.Status.ACTIVE:
            investigation.status = Investigation.Status.ACTIVE
            investigation.finished_at = None
            investigation.save(update_fields=["status", "finished_at"])

        Fact.objects.update_or_create(
            investigation=investigation,
            content="Código E07 observado no desligamento atual.",
            defaults={
                "source_type": Fact.SourceType.TECHNICIAN,
                "created_by": technician,
            },
        )

        past = self._incident(
            organization=organization,
            equipment=equipment,
            description=HISTORY_DESCRIPTION,
            reported_by=technician,
            status=Incident.Status.RESOLVED,
        )
        Intervention.objects.update_or_create(
            incident=past,
            action_taken="Limpeza do duto e restabelecimento do fluxo de ventilação.",
            defaults={
                "technician": technician,
                "confirmed_cause": "Obstrução do fluxo de ar.",
                "result": "Operação normalizada após a limpeza.",
            },
        )

        document, _ = Document.objects.get_or_create(
            organization=organization,
            title=DOCUMENT_TITLE,
            defaults={
                "description": "Documento técnico fictício criado para a demonstração do FaultTrace.",
                "file": DOCUMENT_FILE,
            },
        )
        document.description = (
            "Documento técnico fictício criado para a demonstração do FaultTrace."
        )
        storage = document.file.storage
        if not storage.exists(DOCUMENT_FILE):
            saved_name = storage.save(DOCUMENT_FILE, ContentFile(_demo_pdf()))
            if saved_name != DOCUMENT_FILE:
                raise CommandError("Não foi possível criar o PDF demo no caminho esperado.")
        document.file.name = DOCUMENT_FILE
        document.save(update_fields=["description", "file", "updated_at"])
        document.equipments.add(equipment)
        indexing = index_document(document)
        if indexing.status != "INDEXED":
            raise CommandError("O PDF demo foi criado, mas não pôde ser indexado.")

        historical_evidence, _ = Evidence.objects.update_or_create(
            investigation=investigation,
            content=(
                "Ocorrência anterior semelhante teve obstrução do fluxo de ar confirmada pelo técnico."
            ),
            defaults={
                "source_type": Evidence.SourceType.PAST_INCIDENT,
                "source_incident": past,
                "source_document": None,
                "source_technician": None,
                "source_reference": f"Ocorrência #{past.pk} — Compressor C-04",
                "created_by": technician,
            },
        )
        current_evidence, _ = Evidence.objects.update_or_create(
            investigation=investigation,
            content="Ventilação verificada no equipamento atual e considerada normal.",
            defaults={
                "source_type": Evidence.SourceType.TECHNICIAN,
                "source_incident": None,
                "source_document": None,
                "source_technician": technician,
                "source_reference": "Verificação atual do C-04",
                "created_by": technician,
            },
        )
        hypothesis, _ = Hypothesis.objects.update_or_create(
            investigation=investigation,
            description="Condição térmica anormal.",
            defaults={
                "status": Hypothesis.Status.ACTIVE,
                "created_by": technician,
            },
        )
        HypothesisEvidence.objects.get_or_create(
            hypothesis=hypothesis,
            evidence=historical_evidence,
            relation=HypothesisEvidence.Relation.SUPPORTS,
        )
        HypothesisEvidence.objects.get_or_create(
            hypothesis=hypothesis,
            evidence=current_evidence,
            relation=HypothesisEvidence.Relation.CONTRADICTS,
        )

        self.stdout.write(self.style.SUCCESS("Cenário demo preparado com sucesso."))
        self.stdout.write(f"Organização: {organization.name}")
        self.stdout.write(f"Usuário: {admin.username}")
        self.stdout.write(f"Ocorrência atual: #{current.pk}")
        self.stdout.write(
            "Configure um provider de IA pela interface para executar o Assistente."
        )

    @staticmethod
    def _user(*, username, organization, role):
        user = User.objects.filter(username=username).first()
        if user and user.organization_id != organization.pk:
            raise CommandError(
                f"O usuário {username} já pertence a outra organização."
            )
        if user is None:
            user = User.objects.create(
                username=username,
                organization=organization,
                role=role,
            )
        elif user.role != role:
            user.role = role
            user.save(update_fields=["role"])
        return user

    @staticmethod
    def _incident(*, organization, equipment, description, reported_by, status):
        incident, _ = Incident.objects.get_or_create(
            organization=organization,
            equipment=equipment,
            description=description,
            defaults={"reported_by": reported_by, "status": status},
        )
        changed = []
        if incident.status != status:
            incident.status = status
            changed.append("status")
        if incident.reported_by_id != reported_by.pk:
            incident.reported_by = reported_by
            changed.append("reported_by")
        if changed:
            changed.append("updated_at")
            incident.save(update_fields=changed)
        return incident
