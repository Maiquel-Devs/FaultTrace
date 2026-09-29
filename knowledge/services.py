from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import Evidence, Hypothesis, HypothesisEvidence


class DomainError(Exception):
    pass


def _ensure_user_organization(user, investigation):
    if user.organization_id != investigation.incident.organization_id:
        raise DomainError("O usuário e a investigação devem pertencer à mesma empresa.")


def _validate_and_save(instance):
    try:
        instance.full_clean()
        instance.save()
    except ValidationError as error:
        raise DomainError(" ".join(error.messages)) from error
    return instance


def register_evidence(*, investigation, created_by, **data):
    _ensure_user_organization(created_by, investigation)
    return _validate_and_save(
        Evidence(investigation=investigation, created_by=created_by, **data)
    )


@transaction.atomic
def relate_evidence(*, hypothesis, evidence, relation, user):
    _ensure_user_organization(user, hypothesis.investigation)
    if evidence.investigation_id != hypothesis.investigation_id:
        raise DomainError(
            "A evidência e a hipótese devem pertencer à mesma investigação."
        )
    link = HypothesisEvidence(
        hypothesis=hypothesis,
        evidence=evidence,
        relation=relation,
    )
    try:
        return _validate_and_save(link)
    except (DomainError, IntegrityError) as error:
        if isinstance(error, IntegrityError):
            raise DomainError("Esta relação já foi registrada.") from error
        raise


def update_hypothesis_status(*, hypothesis, status, user):
    _ensure_user_organization(user, hypothesis.investigation)
    hypothesis.status = status
    return _validate_and_save(hypothesis)
