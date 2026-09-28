from django.db import transaction

from accounts.models import User

from .models import Incident, Intervention, Investigation


class DomainError(Exception):
    pass


def _ensure_same_organization(user, organization_id):
    if user.organization_id != organization_id:
        raise DomainError("O usuário e o registro devem pertencer à mesma empresa.")


def create_incident(*, equipment, description, reported_by, occurred_at):
    _ensure_same_organization(reported_by, equipment.organization_id)
    return Incident.objects.create(
        organization=equipment.organization,
        equipment=equipment,
        description=description,
        reported_by=reported_by,
        occurred_at=occurred_at,
    )


@transaction.atomic
def start_investigation(*, incident, technician):
    incident = Incident.objects.select_for_update().get(pk=incident.pk)
    _ensure_same_organization(technician, incident.organization_id)
    if technician.role not in {User.Role.ADMIN, User.Role.TECHNICIAN}:
        raise DomainError("O usuário não pode iniciar investigações.")
    if incident.status != Incident.Status.OPEN:
        raise DomainError("Somente ocorrências abertas podem ser investigadas.")
    if Investigation.objects.filter(incident=incident).exists():
        raise DomainError("Esta ocorrência já possui uma investigação.")

    investigation = Investigation.objects.create(
        incident=incident,
        technician=technician,
    )
    incident.transition_to(Incident.Status.UNDER_INVESTIGATION)
    return investigation


@transaction.atomic
def record_resolution(
    *, incident, technician, action_taken, confirmed_cause, result
):
    incident = Incident.objects.select_for_update().get(pk=incident.pk)
    _ensure_same_organization(technician, incident.organization_id)
    if incident.status != Incident.Status.UNDER_INVESTIGATION:
        raise DomainError("A ocorrência precisa estar em investigação para ser resolvida.")

    investigation = (
        Investigation.objects.select_for_update().filter(incident=incident).first()
    )
    if not investigation or investigation.status != Investigation.Status.ACTIVE:
        raise DomainError("A ocorrência não possui uma investigação ativa.")
    if not all(value.strip() for value in (action_taken, confirmed_cause, result)):
        raise DomainError("Ação, causa confirmada e resultado são obrigatórios.")

    intervention = Intervention.objects.create(
        incident=incident,
        technician=technician,
        action_taken=action_taken,
        confirmed_cause=confirmed_cause,
        result=result,
    )
    investigation.finish()
    incident.transition_to(Incident.Status.RESOLVED)
    return intervention

