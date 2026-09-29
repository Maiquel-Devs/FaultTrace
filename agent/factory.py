from config.crypto import CredentialEncryptionError
from config.models import AIConfiguration

from .errors import LLMConfigurationError
from .providers import (
    AnthropicProvider,
    GeminiProvider,
    MistralProvider,
    OpenAIProvider,
)


PROVIDERS = {
    AIConfiguration.Provider.MISTRAL: MistralProvider,
    AIConfiguration.Provider.OPENAI: OpenAIProvider,
    AIConfiguration.Provider.GEMINI: GeminiProvider,
    AIConfiguration.Provider.ANTHROPIC: AnthropicProvider,
}


def get_active_provider(organization):
    configuration = AIConfiguration.objects.filter(
        organization=organization,
        is_active=True,
    ).first()
    if configuration is None:
        raise LLMConfigurationError(
            "A organização não possui configuração de IA ativa."
        )

    provider_class = PROVIDERS.get(configuration.provider)
    if provider_class is None:
        raise LLMConfigurationError("O provider configurado não é suportado.")
    try:
        api_key = configuration.get_api_key()
    except CredentialEncryptionError as error:
        raise LLMConfigurationError(str(error)) from error
    return provider_class(api_key=api_key, model=configuration.model)
