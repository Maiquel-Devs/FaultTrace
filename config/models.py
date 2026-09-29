from django.db import models

from accounts.models import Organization

from .crypto import decrypt_credential, encrypt_credential


class AIConfiguration(models.Model):
    class Provider(models.TextChoices):
        MISTRAL = "MISTRAL", "Mistral"
        OPENAI = "OPENAI", "OpenAI"
        GEMINI = "GEMINI", "Google Gemini"
        ANTHROPIC = "ANTHROPIC", "Anthropic Claude"

    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="ai_configuration",
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    model = models.CharField(max_length=100)
    api_key_encrypted = models.TextField()
    api_key_last_four = models.CharField(max_length=4)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization_id"]

    def set_api_key(self, api_key):
        self.api_key_encrypted = encrypt_credential(api_key)
        self.api_key_last_four = api_key[-4:] if len(api_key) > 4 else "•" * 4

    def get_api_key(self):
        return decrypt_credential(self.api_key_encrypted)

    @property
    def masked_api_key(self):
        return "•" * 12 + self.api_key_last_four

    def __str__(self):
        return f"{self.organization} — {self.get_provider_display()}"
