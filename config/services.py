from django.core.exceptions import ValidationError
from django.db import transaction

from .crypto import CredentialEncryptionError
from .models import AIConfiguration


class AIConfigurationError(Exception):
    pass


@transaction.atomic
def save_ai_configuration(
    *, organization, provider, model, api_key=None, is_active=True
):
    configuration = (
        AIConfiguration.objects.select_for_update()
        .filter(organization=organization)
        .first()
    )
    if configuration is None:
        configuration = AIConfiguration(organization=organization)
        if not api_key:
            raise AIConfigurationError("Informe uma API key.")
    elif configuration.provider != provider and not api_key:
        raise AIConfigurationError(
            "Informe uma nova API key ao trocar de provider."
        )

    configuration.provider = provider
    configuration.model = model.strip()
    configuration.is_active = is_active
    if api_key:
        try:
            configuration.set_api_key(api_key.strip())
        except CredentialEncryptionError as error:
            raise AIConfigurationError(str(error)) from error

    try:
        configuration.full_clean()
    except ValidationError as error:
        raise AIConfigurationError(" ".join(error.messages)) from error
    configuration.save()
    return configuration
