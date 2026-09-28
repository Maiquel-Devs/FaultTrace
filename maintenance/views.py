from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from .forms import IncidentForm, InterventionForm
from .models import Incident
from .services import DomainError, create_incident, record_resolution, start_investigation


def _organization_incidents(user):
    return Incident.objects.filter(organization=user.organization).select_related(
        "equipment", "reported_by"
    )


@login_required
def incident_list(request):
    return render(
        request,
        "maintenance/incident_list.html",
        {"incidents": _organization_incidents(request.user)},
    )


@login_required
@require_http_methods(["GET", "POST"])
def incident_create(request):
    form = IncidentForm(request.POST or None, organization=request.user.organization)
    if request.method == "POST" and form.is_valid():
        incident = create_incident(
            equipment=form.cleaned_data["equipment"],
            description=form.cleaned_data["description"],
            reported_by=request.user,
            occurred_at=form.cleaned_data["occurred_at"],
        )
        messages.success(request, "Ocorrência registrada.")
        return redirect("incident_detail", pk=incident.pk)
    return render(request, "maintenance/incident_form.html", {"form": form})


@login_required
def incident_detail(request, pk):
    incident = get_object_or_404(_organization_incidents(request.user), pk=pk)
    investigation = getattr(incident, "investigation", None)
    context = {
        "incident": incident,
        "investigation": investigation,
        "interventions": incident.interventions.select_related("technician"),
        "intervention_form": InterventionForm(),
    }
    return render(request, "maintenance/incident_detail.html", context)


@login_required
@require_POST
def investigation_start(request, pk):
    incident = get_object_or_404(_organization_incidents(request.user), pk=pk)
    try:
        start_investigation(incident=incident, technician=request.user)
    except DomainError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Investigação iniciada.")
    return redirect("incident_detail", pk=incident.pk)


@login_required
@require_POST
def intervention_create(request, pk):
    incident = get_object_or_404(_organization_incidents(request.user), pk=pk)
    form = InterventionForm(request.POST)
    if form.is_valid():
        try:
            record_resolution(
                incident=incident,
                technician=request.user,
                **form.cleaned_data,
            )
        except DomainError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Intervenção registrada e ocorrência resolvida.")
            return redirect("incident_detail", pk=incident.pk)

    investigation = getattr(incident, "investigation", None)
    return render(
        request,
        "maintenance/incident_detail.html",
        {
            "incident": incident,
            "investigation": investigation,
            "interventions": incident.interventions.select_related("technician"),
            "intervention_form": form,
        },
        status=400,
    )

