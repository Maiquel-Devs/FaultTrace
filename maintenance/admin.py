from django.contrib import admin

from .models import Incident, Intervention, Investigation


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ("id", "equipment", "organization", "status", "occurred_at")
    list_filter = ("organization", "status")
    search_fields = ("equipment__code", "equipment__name", "description")
    readonly_fields = ("status", "created_at", "updated_at")


@admin.register(Investigation)
class InvestigationAdmin(admin.ModelAdmin):
    list_display = ("incident", "technician", "status", "started_at", "finished_at")
    list_filter = ("status", "incident__organization")
    readonly_fields = ("status", "started_at", "finished_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Intervention)
class InterventionAdmin(admin.ModelAdmin):
    list_display = ("incident", "technician", "created_at")
    list_filter = ("incident__organization",)
    search_fields = ("action_taken", "confirmed_cause", "result")
    readonly_fields = ("created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
