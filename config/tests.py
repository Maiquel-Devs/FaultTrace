from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Organization, User
from agent.contracts import LLMMessage
from agent.errors import LLMConfigurationError, LLMProviderError
from agent.factory import get_active_provider
from agent.providers import (
    AnthropicProvider,
    GeminiProvider,
    MistralProvider,
    OpenAIProvider,
)

from .models import AIConfiguration
from .services import save_ai_configuration


TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()


@override_settings(AI_CREDENTIAL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class AIConfigurationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Indústria Alfa")
        self.other_organization = Organization.objects.create(name="Indústria Beta")
        self.admin = User.objects.create_user(
            username="admin-ai",
            password="test-password",
            organization=self.organization,
            role=User.Role.ADMIN,
        )
        self.technician = User.objects.create_user(
            username="technician-ai",
            password="test-password",
            organization=self.organization,
            role=User.Role.TECHNICIAN,
        )
        self.other_admin = User.objects.create_user(
            username="other-admin-ai",
            password="test-password",
            organization=self.other_organization,
            role=User.Role.ADMIN,
        )

    def _create_configuration(
        self,
        *,
        organization=None,
        provider=AIConfiguration.Provider.MISTRAL,
        model="mistral-small-latest",
        api_key="mistral-secret-7K2A",
    ):
        return save_ai_configuration(
            organization=organization or self.organization,
            provider=provider,
            model=model,
            api_key=api_key,
            is_active=True,
        )

    def test_api_key_is_encrypted_masked_and_never_rendered(self):
        secret = "mistral-secret-7K2A"
        configuration = self._create_configuration(api_key=secret)

        self.assertNotIn(secret, configuration.api_key_encrypted)
        self.assertEqual(configuration.get_api_key(), secret)
        self.assertEqual(configuration.masked_api_key, "•" * 12 + "7K2A")

        self.client.force_login(self.admin)
        response = self.client.get(reverse("ai_configuration"))
        self.assertContains(response, configuration.masked_api_key)
        self.assertNotContains(response, secret)
        self.assertNotContains(response, configuration.api_key_encrypted)

    def test_short_api_key_is_fully_masked(self):
        configuration = self._create_configuration(api_key="tiny")

        self.assertEqual(configuration.api_key_last_four, "•" * 4)
        self.assertEqual(configuration.masked_api_key, "•" * 16)
        self.assertNotIn("tiny", configuration.masked_api_key)

    def test_technician_cannot_access_ai_configuration(self):
        self.client.force_login(self.technician)
        response = self.client.get(reverse("ai_configuration"))
        self.assertEqual(response.status_code, 403)

    def test_configuration_page_is_scoped_to_user_organization(self):
        self._create_configuration(model="model-alpha")
        other = self._create_configuration(
            organization=self.other_organization,
            model="model-beta",
            api_key="other-secret-BETA",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("ai_configuration"))

        self.assertContains(response, "model-alpha")
        self.assertNotContains(response, "model-beta")
        self.assertNotContains(response, other.masked_api_key)

    def test_factory_returns_all_supported_adapters(self):
        cases = (
            (AIConfiguration.Provider.MISTRAL, MistralProvider),
            (AIConfiguration.Provider.OPENAI, OpenAIProvider),
            (AIConfiguration.Provider.GEMINI, GeminiProvider),
            (AIConfiguration.Provider.ANTHROPIC, AnthropicProvider),
        )
        for provider_name, expected_class in cases:
            with self.subTest(provider=provider_name):
                self._create_configuration(
                    provider=provider_name,
                    model=f"model-{provider_name.lower()}",
                    api_key=f"secret-{provider_name}",
                )
                provider = get_active_provider(self.organization)
                self.assertIsInstance(provider, expected_class)

    def test_factory_reports_missing_and_invalid_configuration(self):
        with self.assertRaisesRegex(LLMConfigurationError, "não possui"):
            get_active_provider(self.organization)

        configuration = self._create_configuration()
        AIConfiguration.objects.filter(pk=configuration.pk).update(provider="INVALID")
        with self.assertRaisesRegex(LLMConfigurationError, "não é suportado"):
            get_active_provider(self.organization)

    def test_provider_error_does_not_expose_api_key(self):
        secret = "mistral-super-secret"
        provider = MistralProvider(api_key=secret, model="invalid-model")
        with patch(
            "agent.providers.Mistral",
            side_effect=RuntimeError(f"authentication failed with {secret}"),
        ):
            with self.assertRaises(LLMProviderError) as captured:
                provider.generate([LLMMessage(role="user", content="Olá")])

        self.assertNotIn(secret, str(captured.exception))
        self.assertNotIn(secret, repr(captured.exception))
        self.assertIsNone(captured.exception.__cause__)

    def test_only_one_configuration_can_exist_per_organization(self):
        self._create_configuration()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AIConfiguration.objects.create(
                organization=self.organization,
                provider=AIConfiguration.Provider.OPENAI,
                model="another-model",
                api_key_encrypted="encrypted",
                api_key_last_four="test",
            )

    def test_all_adapters_normalize_responses(self):
        message = [LLMMessage(role="user", content="Olá")]

        with patch("agent.providers.Mistral") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.chat.complete.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="mistral ok"))]
            )
            response = MistralProvider(api_key="key", model="model").generate(message)
            self.assertEqual(response.content, "mistral ok")

        with patch("agent.providers.OpenAI") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.responses.create.return_value = SimpleNamespace(output_text="openai ok")
            response = OpenAIProvider(api_key="key", model="model").generate(message)
            self.assertEqual(response.content, "openai ok")

        with patch("agent.providers.genai.Client") as sdk:
            client = sdk.return_value
            client.models.generate_content.return_value = SimpleNamespace(text="gemini ok")
            response = GeminiProvider(api_key="key", model="model").generate(message)
            self.assertEqual(response.content, "gemini ok")
            client.close.assert_called_once()

        with patch("agent.providers.Anthropic") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.messages.create.return_value = SimpleNamespace(
                content=[SimpleNamespace(type="text", text="anthropic ok")]
            )
            response = AnthropicProvider(api_key="key", model="model").generate(message)
            self.assertEqual(response.content, "anthropic ok")

    def test_admin_can_retain_key_and_test_connection(self):
        configuration = self._create_configuration()
        original_encrypted_key = configuration.api_key_encrypted
        provider = MagicMock()
        self.client.force_login(self.admin)

        with patch("config.views.get_active_provider", return_value=provider):
            response = self.client.post(
                reverse("ai_configuration"),
                {
                    "provider": AIConfiguration.Provider.MISTRAL,
                    "model": "mistral-medium-latest",
                    "api_key": "",
                    "is_active": "on",
                    "action": "test",
                },
                follow=True,
            )

        self.assertContains(response, "Conexão realizada com sucesso.")
        provider.test_connection.assert_called_once_with()
        configuration.refresh_from_db()
        self.assertEqual(configuration.api_key_encrypted, original_encrypted_key)
        self.assertEqual(configuration.model, "mistral-medium-latest")
