from django.conf import settings
from django.db import models

from accounts.models import Organization


class Equipment(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Ativo"
        INACTIVE = "INACTIVE", "Inativo"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="equipments",
    )
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=50)
    manufacturer = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=150, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                name="unique_equipment_code_per_organization",
            )
        ]

    def __str__(self):
        return f"{self.code} — {self.name}"


class Document(models.Model):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="documents",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to="documents/%Y/%m/")
    equipments = models.ManyToManyField(Equipment, related_name="documents", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

