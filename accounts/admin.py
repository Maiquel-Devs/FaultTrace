from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Organization, User


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(User)
class FaultTraceUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("FaultTrace", {"fields": ("organization", "role")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("FaultTrace", {"fields": ("organization", "role")}),
    )
    list_display = ("username", "email", "organization", "role", "is_staff")
    list_filter = UserAdmin.list_filter + ("organization", "role")

