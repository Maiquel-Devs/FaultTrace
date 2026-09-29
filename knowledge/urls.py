from django.urls import path

from . import views


urlpatterns = [
    path(
        "<int:incident_pk>/knowledge/",
        views.related_knowledge,
        name="related_knowledge",
    ),
    path("<int:incident_pk>/facts/new/", views.fact_create, name="fact_create"),
    path(
        "<int:incident_pk>/evidence/new/",
        views.evidence_create,
        name="evidence_create",
    ),
    path(
        "<int:incident_pk>/hypotheses/new/",
        views.hypothesis_create,
        name="hypothesis_create",
    ),
    path("hypotheses/<int:pk>/", views.hypothesis_manage, name="hypothesis_manage"),
    path(
        "hypotheses/<int:pk>/status/",
        views.hypothesis_status_update,
        name="hypothesis_status_update",
    ),
    path(
        "hypotheses/<int:pk>/evidence/",
        views.hypothesis_evidence_create,
        name="hypothesis_evidence_create",
    ),
]
