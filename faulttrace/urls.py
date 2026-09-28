from django.contrib import admin
from django.urls import path

from .views import health, home


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("", home, name="home"),
]

