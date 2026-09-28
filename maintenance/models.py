from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from accounts.models import Organization
from assets.models import Equipment


class Incident(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Aberta"
        UNDER_INVESTIGATION = "UNDER_INVESTIGATION", "Em investigação"
        RESOLVED = "RESOLVED", "Resolvida"
        CLOSED = "CLOSED", "Encerrada"

    ALLOWED_TRANSITIONS = {
        Status.OPEN: {Status.UNDER_INVESTIGATION},
        Status.UNDER_INVESTIGATION: {Status.RESOLVED},
        Status.RESOLVED: {Status.CLOSED},
        Status.CLOSED: set(),
    }

    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="incidents",
    )
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.PROTECT,
        related_name="incidents",
    )
    description = models.TextField()
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.OPEN)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reported_incidents",
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_at"]

    def clean(self):
        super().clean()
        if self.equipment_id and self.organization_id:
            if self.equipment.organization_id != self.organization_id:
                raise ValidationError(
                    {"equipment": "O equipamento deve pertencer à mesma empresa."}
                )
        if self.reported_by_id and self.organization_id:
            if self.reported_by.organization_id != self.organization_id:
                raise ValidationError(
                    {"reported_by": "O usuário deve pertencer à mesma empresa."}
                )

    def transition_to(self, new_status):
        if new_status not in self.ALLOWED_TRANSITIONS[self.status]:
            raise ValidationError(
                f"Transição de {self.get_status_display()} para {new_status} não permitida."
            )
        self.status = new_status
        self.save(update_fields=["status", "updated_at"])

    def __str__(self):
        return f"Ocorrência #{self.pk} — {self.equipment}"


class Investigation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Ativa"
        FINISHED = "FINISHED", "Finalizada"

    incident = models.OneToOneField(
        Incident,
        on_delete=models.PROTECT,
        related_name="investigation",
    )
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="investigations",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def clean(self):
        super().clean()
        if self.incident_id and self.technician_id:
            if self.incident.organization_id != self.technician.organization_id:
                raise ValidationError(
                    {"technician": "O técnico deve pertencer à mesma empresa."}
                )

    def finish(self):
        if self.status != self.Status.ACTIVE:
            raise ValidationError("A investigação já foi finalizada.")
        self.status = self.Status.FINISHED
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "finished_at"])

    def __str__(self):
        return f"Investigação da ocorrência #{self.incident_id}"


class Intervention(models.Model):
    incident = models.ForeignKey(
        Incident,
        on_delete=models.PROTECT,
        related_name="interventions",
    )
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="interventions",
    )
    action_taken = models.TextField()
    confirmed_cause = models.TextField()
    result = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        if self.incident_id and self.technician_id:
            if self.incident.organization_id != self.technician.organization_id:
                raise ValidationError(
                    {"technician": "O técnico deve pertencer à mesma empresa."}
                )

    def __str__(self):
        return f"Intervenção na ocorrência #{self.incident_id}"
