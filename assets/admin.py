from django.contrib import admin

from .models import Document, Equipment


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "organization", "status", "location")
    list_filter = ("organization", "status")
    search_fields = ("code", "name", "manufacturer", "model")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "organization", "created_at")
    list_filter = ("organization",)
    search_fields = ("title", "description")
    filter_horizontal = ("equipments",)

