from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from maintenance.models import Investigation

from .forms import (
    EvidenceForm,
    FactForm,
    HypothesisEvidenceForm,
    HypothesisForm,
    HypothesisStatusForm,
)
from .models import Hypothesis
from .services import (
    DomainError,
    register_evidence,
    relate_evidence,
    update_hypothesis_status,
)


def _organization_investigations(user):
    return Investigation.objects.filter(
        incident__organization=user.organization
    ).select_related("incident", "incident__equipment")


def _investigation_for_incident(user, incident_pk):
    return get_object_or_404(
        _organization_investigations(user), incident_id=incident_pk
    )


@login_required
@require_http_methods(["GET", "POST"])
def fact_create(request, incident_pk):
    investigation = _investigation_for_incident(request.user, incident_pk)
    form = FactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        fact = form.save(commit=False)
        fact.investigation = investigation
        fact.created_by = request.user
        try:
            fact.full_clean()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            fact.save()
            messages.success(request, "Fato registrado.")
            return redirect("incident_detail", pk=incident_pk)
    return render(
        request,
        "knowledge/form.html",
        {"form": form, "title": "Adicionar fato", "incident": investigation.incident},
    )


@login_required
@require_http_methods(["GET", "POST"])
def evidence_create(request, incident_pk):
    investigation = _investigation_for_incident(request.user, incident_pk)
    form = EvidenceForm(request.POST or None, investigation=investigation)
    if request.method == "POST" and form.is_valid():
        try:
            register_evidence(
                investigation=investigation,
                created_by=request.user,
                **form.cleaned_data,
            )
        except DomainError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Evidência registrada com sua proveniência.")
            return redirect("incident_detail", pk=incident_pk)
    return render(
        request,
        "knowledge/evidence_form.html",
        {"form": form, "title": "Adicionar evidência", "incident": investigation.incident},
    )


@login_required
@require_http_methods(["GET", "POST"])
def hypothesis_create(request, incident_pk):
    investigation = _investigation_for_incident(request.user, incident_pk)
    form = HypothesisForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        hypothesis = form.save(commit=False)
        hypothesis.investigation = investigation
        hypothesis.created_by = request.user
        try:
            hypothesis.full_clean()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            hypothesis.save()
            messages.success(request, "Hipótese criada.")
            return redirect("hypothesis_manage", pk=hypothesis.pk)
    return render(
        request,
        "knowledge/form.html",
        {"form": form, "title": "Criar hipótese", "incident": investigation.incident},
    )


def _organization_hypotheses(user):
    return Hypothesis.objects.filter(
        investigation__incident__organization=user.organization
    ).select_related("investigation__incident", "created_by")


@login_required
def hypothesis_manage(request, pk):
    hypothesis = get_object_or_404(_organization_hypotheses(request.user), pk=pk)
    links = hypothesis.evidence_links.select_related(
        "evidence__source_document",
        "evidence__source_incident",
        "evidence__source_technician",
    )
    return render(
        request,
        "knowledge/hypothesis_manage.html",
        {
            "hypothesis": hypothesis,
            "supports": links.filter(relation="SUPPORTS"),
            "contradicts": links.filter(relation="CONTRADICTS"),
            "status_form": HypothesisStatusForm(instance=hypothesis),
            "relation_form": HypothesisEvidenceForm(
                investigation=hypothesis.investigation
            ),
        },
    )


@login_required
@require_POST
def hypothesis_status_update(request, pk):
    hypothesis = get_object_or_404(_organization_hypotheses(request.user), pk=pk)
    form = HypothesisStatusForm(request.POST, instance=hypothesis)
    if form.is_valid():
        try:
            update_hypothesis_status(
                hypothesis=hypothesis,
                status=form.cleaned_data["status"],
                user=request.user,
            )
        except DomainError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Status da hipótese atualizado.")
    else:
        messages.error(request, "Status inválido.")
    return redirect("hypothesis_manage", pk=pk)


@login_required
@require_POST
def hypothesis_evidence_create(request, pk):
    hypothesis = get_object_or_404(_organization_hypotheses(request.user), pk=pk)
    form = HypothesisEvidenceForm(
        request.POST,
        investigation=hypothesis.investigation,
    )
    if form.is_valid():
        try:
            relate_evidence(
                hypothesis=hypothesis,
                evidence=form.cleaned_data["evidence"],
                relation=form.cleaned_data["relation"],
                user=request.user,
            )
        except DomainError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Evidência associada à hipótese.")
    else:
        messages.error(request, "Selecione uma evidência válida desta investigação.")
    return redirect("hypothesis_manage", pk=pk)
