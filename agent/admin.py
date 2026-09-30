from django.contrib import admin

from .models import AgentInteraction


@admin.register(AgentInteraction)
class AgentInteractionAdmin(admin.ModelAdmin):
    list_display = ("id", "investigation", "user", "provider", "model", "created_at")
    list_filter = ("provider", "created_at")
    search_fields = ("question", "response", "user__username")
    readonly_fields = ("created_at",)
