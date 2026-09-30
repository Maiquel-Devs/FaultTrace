from django.conf import settings
from django.db import models

from maintenance.models import Investigation


class AgentInteraction(models.Model):
    investigation = models.ForeignKey(
        Investigation,
        on_delete=models.PROTECT,
        related_name="agent_interactions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="agent_interactions",
    )
    question = models.TextField()
    response = models.TextField()
    provider = models.CharField(max_length=20)
    model = models.CharField(max_length=100)
    tools_used = models.JSONField(default=list, blank=True)
    sources = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"Interação #{self.pk} — investigação {self.investigation_id}"
