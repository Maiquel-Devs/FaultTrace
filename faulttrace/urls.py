from django.contrib import admin
from django.urls import include, path

from .views import health, home


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("equipments/", include("assets.equipment_urls")),
    path("documents/", include("assets.document_urls")),
    path("settings/", include("config.urls")),
    path("incidents/", include("knowledge.urls")),
    path("incidents/", include("maintenance.urls")),
    path("health/", health, name="health"),
    path("", home, name="home"),
]
