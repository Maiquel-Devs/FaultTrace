from django.urls import path

from . import views


urlpatterns = [
    path("ai/", views.ai_configuration, name="ai_configuration"),
]
