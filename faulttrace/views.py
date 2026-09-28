from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from assets.models import Document, Equipment
from maintenance.models import Incident


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


@require_GET
@login_required
def home(request):
    organization = request.user.organization
    context = {
        "equipment_count": Equipment.objects.filter(organization=organization).count(),
        "document_count": Document.objects.filter(organization=organization).count(),
        "open_incident_count": Incident.objects.filter(
            organization=organization,
            status__in=[Incident.Status.OPEN, Incident.Status.UNDER_INVESTIGATION],
        ).count(),
    }
    return render(request, "home.html", context)
