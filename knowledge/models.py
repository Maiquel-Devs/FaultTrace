from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from assets.models import Document
from maintenance.models import Incident, Investigation


def _investigation_organization_id(investigation):
    return investigation.incident.organization_id


class Fact(models.Model):
    class SourceType(models.TextChoices):
        TECHNICIAN = "TECHNICIAN", "Técnico"
        SYSTEM = "SYSTEM", "Sistema"

    investigation = models.ForeignKey(
        Investigation,
        on_delete=models.PROTECT,
        related_name="facts",
    )
    content = models.TextField()
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_facts",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def clean(self):
        super().clean()
        errors = {}
        if self.source_type == self.SourceType.TECHNICIAN and not self.created_by_id:
            errors["created_by"] = "Fatos informados por técnico exigem um usuário."
        if self.investigation_id and self.created_by_id:
            if self.created_by.organization_id != _investigation_organization_id(
                self.investigation
            ):
                errors["created_by"] = "O usuário deve pertencer à mesma empresa."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.content[:80]


class Evidence(models.Model):
    class SourceType(models.TextChoices):
        DOCUMENT = "DOCUMENT", "Documento"
        PAST_INCIDENT = "PAST_INCIDENT", "Ocorrência anterior"
        TECHNICIAN = "TECHNICIAN", "Técnico"

    investigation = models.ForeignKey(
        Investigation,
        on_delete=models.PROTECT,
        related_name="evidence",
    )
    content = models.TextField()
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    source_document = models.ForeignKey(
        Document,
        on_delete=models.PROTECT,
        related_name="knowledge_evidence",
        null=True,
        blank=True,
    )
    source_incident = models.ForeignKey(
        Incident,
        on_delete=models.PROTECT,
        related_name="knowledge_evidence",
        null=True,
        blank=True,
    )
    source_technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="provided_evidence",
        null=True,
        blank=True,
    )
    source_reference = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_evidence",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        source_type="DOCUMENT",
                        source_document__isnull=False,
                        source_incident__isnull=True,
                        source_technician__isnull=True,
                    )
                    | Q(
                        source_type="PAST_INCIDENT",
                        source_document__isnull=True,
                        source_incident__isnull=False,
                        source_technician__isnull=True,
                    )
                    | Q(
                        source_type="TECHNICIAN",
                        source_document__isnull=True,
                        source_incident__isnull=True,
                        source_technician__isnull=False,
                    )
                ),
                name="evidence_has_coherent_provenance",
            )
        ]

    def clean(self):
        super().clean()
        errors = {}
        selected_sources = {
            self.SourceType.DOCUMENT: self.source_document_id,
            self.SourceType.PAST_INCIDENT: self.source_incident_id,
            self.SourceType.TECHNICIAN: self.source_technician_id,
        }
        expected_source = selected_sources.get(self.source_type)
        if not expected_source:
            errors["source_type"] = "Informe a fonte correspondente ao tipo selecionado."

        populated = [source for source in selected_sources.values() if source]
        if len(populated) > 1:
            errors["source_type"] = "Informe somente a fonte correspondente ao tipo."

        if self.investigation_id:
            organization_id = _investigation_organization_id(self.investigation)
            if (
                self.source_document_id
                and self.source_document.organization_id != organization_id
            ):
                errors["source_document"] = (
                    "O documento deve pertencer à mesma empresa."
                )
            if (
                self.source_incident_id
                and self.source_incident.organization_id != organization_id
            ):
                errors["source_incident"] = (
                    "A ocorrência deve pertencer à mesma empresa."
                )
            if self.source_incident_id == self.investigation.incident_id:
                errors["source_incident"] = "Selecione uma ocorrência anterior."
            if (
                self.source_technician_id
                and self.source_technician.organization_id != organization_id
            ):
                errors["source_technician"] = (
                    "O técnico deve pertencer à mesma empresa."
                )
            elif (
                self.source_technician_id
                and self.source_technician.role != "TECHNICIAN"
            ):
                errors["source_technician"] = (
                    "A fonte selecionada deve possuir o papel de técnico."
                )
            if self.created_by_id and self.created_by.organization_id != organization_id:
                errors["created_by"] = "O usuário deve pertencer à mesma empresa."
        if errors:
            raise ValidationError(errors)

    @property
    def source_label(self):
        if self.source_type == self.SourceType.DOCUMENT:
            base = self.source_document.title
        elif self.source_type == self.SourceType.PAST_INCIDENT:
            base = f"Ocorrência #{self.source_incident_id}"
        else:
            base = str(self.source_technician)
        return f"{base} — {self.source_reference}" if self.source_reference else base

    def __str__(self):
        return self.content[:80]


class Hypothesis(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Ativa"
        WEAKENED = "WEAKENED", "Enfraquecida"
        DISCARDED = "DISCARDED", "Descartada"
        CONFIRMED_BY_TECHNICIAN = (
            "CONFIRMED_BY_TECHNICIAN",
            "Confirmada pelo técnico",
        )

    investigation = models.ForeignKey(
        Investigation,
        on_delete=models.PROTECT,
        related_name="hypotheses",
    )
    description = models.TextField()
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_hypotheses",
    )
    evidence = models.ManyToManyField(
        Evidence,
        through="HypothesisEvidence",
        related_name="hypotheses",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def clean(self):
        super().clean()
        if self.investigation_id and self.created_by_id:
            if self.created_by.organization_id != _investigation_organization_id(
                self.investigation
            ):
                raise ValidationError(
                    {"created_by": "O usuário deve pertencer à mesma empresa."}
                )

    def __str__(self):
        return self.description[:80]


class HypothesisEvidence(models.Model):
    class Relation(models.TextChoices):
        SUPPORTS = "SUPPORTS", "Sustenta"
        CONTRADICTS = "CONTRADICTS", "Contradiz"

    hypothesis = models.ForeignKey(
        Hypothesis,
        on_delete=models.CASCADE,
        related_name="evidence_links",
    )
    evidence = models.ForeignKey(
        Evidence,
        on_delete=models.CASCADE,
        related_name="hypothesis_links",
    )
    relation = models.CharField(max_length=15, choices=Relation.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["hypothesis", "evidence", "relation"],
                name="unique_evidence_relation_per_hypothesis",
            )
        ]

    def clean(self):
        super().clean()
        if self.hypothesis_id and self.evidence_id:
            if self.hypothesis.investigation_id != self.evidence.investigation_id:
                raise ValidationError(
                    "A evidência e a hipótese devem pertencer à mesma investigação."
                )

    def __str__(self):
        return f"{self.evidence} {self.get_relation_display()} {self.hypothesis}"


class DocumentSection(models.Model):
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="sections",
    )
    page_number = models.PositiveIntegerField()
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document_id", "page_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "page_number"],
                name="unique_document_page_section",
            )
        ]

    @property
    def reference(self):
        return f"{self.document.title} — página {self.page_number}"

    def __str__(self):
        return self.reference
