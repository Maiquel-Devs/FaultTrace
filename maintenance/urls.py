from django.urls import path

from . import views


urlpatterns = [
    path("", views.incident_list, name="incident_list"),
    path("new/", views.incident_create, name="incident_create"),
    path("<int:pk>/", views.incident_detail, name="incident_detail"),
    path(
        "<int:pk>/start-investigation/",
        views.investigation_start,
        name="investigation_start",
    ),
    path(
        "<int:pk>/interventions/new/",
        views.intervention_create,
        name="intervention_create",
    ),
    path(
        "<int:pk>/agent/investigate/",
        views.agent_investigate,
        name="agent_investigate",
    ),
]
