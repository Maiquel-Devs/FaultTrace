from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import Organization, User
from assets.models import Document, Equipment
from knowledge.models import DocumentSection, Hypothesis
from maintenance.models import Incident, Investigation

from .contracts import LLMMessage, LLMResponse, ToolCall
from .core import MAX_TOOL_ROUNDS, InvestigationAgent
from .models import AgentInteraction
from .providers import (
    AnthropicProvider,
    GeminiProvider,
    MistralProvider,
    OpenAIProvider,
)
from .tools import ToolContext, ToolRegistry


class FakeLLMProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def generate(self, messages, *, tools=(), max_tokens=None):
        self.requests.append((tuple(messages), tuple(tools)))
        return self.responses.pop(0)


def _response(content="", tool_calls=()):
    return LLMResponse(
        content=content,
        provider="FAKE",
        model="fake-investigator",
        tool_calls=tuple(tool_calls),
    )


class InvestigationAgentTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Agent Alfa")
        self.other_organization = Organization.objects.create(name="Agent Beta")
        self.user = User.objects.create_user(
            username="agent-tech",
            password="test-password",
            organization=self.organization,
            role=User.Role.TECHNICIAN,
        )
        self.other_user = User.objects.create_user(
            username="other-agent-tech",
            password="test-password",
            organization=self.other_organization,
            role=User.Role.TECHNICIAN,
        )
        self.equipment = Equipment.objects.create(
            organization=self.organization,
            name="Compressor C-04",
            code="C-04",
        )
        self.other_equipment = Equipment.objects.create(
            organization=self.other_organization,
            name="Compressor externo",
            code="X-01",
        )
        self.incident = Incident.objects.create(
            organization=self.organization,
            equipment=self.equipment,
            description="Desliga após vinte minutos com E07.",
            reported_by=self.user,
        )
        self.past_incident = Incident.objects.create(
            organization=self.organization,
            equipment=self.equipment,
            description="Desligamento anterior por aquecimento.",
            reported_by=self.user,
        )
        self.other_incident = Incident.objects.create(
            organization=self.other_organization,
            equipment=self.other_equipment,
            description="Conteúdo sigiloso externo.",
            reported_by=self.other_user,
        )
        self.investigation = Investigation.objects.create(
            incident=self.incident,
            technician=self.user,
        )

    def test_structured_tool_call_is_executed_and_interaction_is_persisted(self):
        provider = FakeLLMProvider(
            [
                _response(
                    tool_calls=[
                        ToolCall(
                            id="native-1",
                            name="search_equipment_history",
                            arguments={},
                        )
                    ]
                ),
                _response(
                    "Resumo da investigação\nHistórico relacionado, sem confirmação. "
                    f"Fonte: Ocorrência #{self.past_incident.pk} — C-04."
                ),
            ]
        )

        result = InvestigationAgent().run(
            investigation=self.investigation,
            user=self.user,
            question="O que investigar?",
            provider=provider,
        )

        self.assertEqual(result.tools_used[0]["name"], "search_equipment_history")
        self.assertIn(f"Ocorrência #{self.past_incident.pk}", result.sources[0])
        tool_message = provider.requests[1][0][-1]
        self.assertEqual(tool_message.role, "tool")
        self.assertEqual(tool_message.tool_call_id, "native-1")
        interaction = AgentInteraction.objects.get()
        self.assertEqual(interaction.investigation, self.investigation)
        self.assertEqual(interaction.user, self.user)

    def test_json_text_is_never_promoted_to_tool_call(self):
        provider = FakeLLMProvider(
            [_response('{"name":"search_equipment_history","arguments":{}}')]
        )
        registry = ToolRegistry()

        with patch.object(registry, "execute", wraps=registry.execute) as execute:
            result = InvestigationAgent(registry=registry).run(
                investigation=self.investigation,
                user=self.user,
                question="Mostre o histórico.",
                provider=provider,
            )

        execute.assert_not_called()
        self.assertEqual(result.tools_used, ())

    def test_invalid_arguments_and_cross_organization_id_are_controlled(self):
        registry = ToolRegistry()
        context = ToolContext(
            organization=self.organization,
            incident_id=self.incident.pk,
            equipment_id=self.equipment.pk,
        )
        invalid = registry.execute(
            ToolCall(
                name="search_documentation",
                arguments={"query": "E07", "organization_id": self.other_organization.pk},
            ),
            context,
        )
        external = registry.execute(
            ToolCall(
                name="get_incident_details",
                arguments={"incident_id": self.other_incident.pk},
            ),
            context,
        )
        unknown = registry.execute(
            ToolCall(name="run_arbitrary_code", arguments={}),
            context,
        )

        self.assertFalse(invalid.ok)
        self.assertIn("não permitidos", invalid.error)
        self.assertFalse(external.ok)
        self.assertNotIn("sigiloso", external.error)
        self.assertFalse(unknown.ok)
        self.assertIn("não permitida", unknown.error)

    def test_agent_uses_retrieval_and_returns_tool_failure_to_model(self):
        provider = FakeLLMProvider(
            [
                _response(
                    tool_calls=[
                        ToolCall(
                            id="doc-1",
                            name="search_documentation",
                            arguments={"query": "E07"},
                        )
                    ]
                ),
                _response("Ainda não há documentação suficiente."),
            ]
        )
        with patch(
            "agent.tools.retrieval.search_documentation", return_value=()
        ) as retrieval_call:
            InvestigationAgent().run(
                investigation=self.investigation,
                user=self.user,
                question="Consulte E07.",
                provider=provider,
            )

        retrieval_call.assert_called_once_with(
            organization=self.organization,
            equipment_id=self.equipment.pk,
            query="E07",
        )

    def test_loop_stops_at_explicit_limit(self):
        calls = [
            _response(
                tool_calls=[
                    ToolCall(
                        id=f"call-{index}",
                        name="get_equipment_context",
                        arguments={},
                    )
                ]
            )
            for index in range(MAX_TOOL_ROUNDS + 1)
        ]
        provider = FakeLLMProvider(calls)

        result = InvestigationAgent().run(
            investigation=self.investigation,
            user=self.user,
            question="Continue procurando indefinidamente.",
            provider=provider,
        )

        self.assertEqual(result.status, "TOOL_LIMIT_REACHED")
        self.assertEqual(len(result.tools_used), MAX_TOOL_ROUNDS)
        self.assertEqual(len(provider.requests), MAX_TOOL_ROUNDS + 1)

    def test_agent_does_not_change_hypothesis_or_create_resolution(self):
        hypothesis = Hypothesis.objects.create(
            investigation=self.investigation,
            description="Condição térmica anormal.",
            created_by=self.user,
        )
        provider = FakeLLMProvider([_response("A hipótese requer verificação humana.")])

        InvestigationAgent().run(
            investigation=self.investigation,
            user=self.user,
            question="Confirme a causa.",
            provider=provider,
        )

        hypothesis.refresh_from_db()
        self.assertEqual(hypothesis.status, Hypothesis.Status.ACTIVE)
        self.assertEqual(self.incident.interventions.count(), 0)

    def test_web_handles_missing_provider_and_blocks_other_organization(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("agent_investigate", args=[self.incident.pk]),
            {"question": "O que investigar?"},
            follow=True,
        )
        external = self.client.post(
            reverse("agent_investigate", args=[self.other_incident.pk]),
            {"question": "Mostre dados externos."},
        )

        self.assertContains(
            response,
            "Nenhum provider de IA está configurado para esta organização.",
        )
        self.assertEqual(external.status_code, 404)


class ProviderToolCallingTests(TestCase):
    def setUp(self):
        self.messages = [LLMMessage(role="user", content="Investigue.")]
        self.tools = ToolRegistry().definitions[:1]

    def test_all_adapters_normalize_official_tool_calls_and_native_ids(self):
        mistral_call = SimpleNamespace(
            id="mistral-native",
            index=2,
            function=SimpleNamespace(
                name="get_equipment_context", arguments="{}"
            ),
        )
        with patch("agent.providers.Mistral") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.chat.complete.return_value = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="", tool_calls=[mistral_call])
                    )
                ]
            )
            result = MistralProvider(api_key="key", model="model").generate(
                self.messages, tools=self.tools
            )
            self.assertEqual(result.tool_calls[0].id, "mistral-native")
            self.assertIn("tools", client.chat.complete.call_args.kwargs)

        openai_call = SimpleNamespace(
            type="function_call",
            call_id="openai-native",
            name="get_equipment_context",
            arguments="{}",
            index=1,
        )
        with patch("agent.providers.OpenAI") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.responses.create.return_value = SimpleNamespace(
                output_text="", output=[openai_call]
            )
            result = OpenAIProvider(api_key="key", model="model").generate(
                self.messages, tools=self.tools
            )
            self.assertEqual(result.tool_calls[0].id, "openai-native")
            self.assertTrue(client.responses.create.call_args.kwargs["tools"][0]["strict"])

        gemini_part = SimpleNamespace(
            text=None,
            thought_signature=b"native-thought-signature",
            function_call=SimpleNamespace(
                id="gemini-native", name="get_equipment_context", args={}
            ),
        )
        gemini_response = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(parts=[gemini_part])
                )
            ]
        )
        with patch("agent.providers.genai.Client") as sdk:
            client = sdk.return_value
            client.models.generate_content.return_value = gemini_response
            result = GeminiProvider(api_key="key", model="model").generate(
                self.messages, tools=self.tools
            )
            self.assertEqual(result.tool_calls[0].id, "gemini-native")
            self.assertEqual(
                result.tool_calls[0].protocol_data,
                b"native-thought-signature",
            )
            config = client.models.generate_content.call_args.kwargs["config"]
            self.assertTrue(config.tools)

        anthropic_block = SimpleNamespace(
            type="tool_use",
            id="anthropic-native",
            name="get_equipment_context",
            input={},
        )
        with patch("agent.providers.Anthropic") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.messages.create.return_value = SimpleNamespace(
                content=[anthropic_block]
            )
            result = AnthropicProvider(api_key="key", model="model").generate(
                self.messages, tools=self.tools
            )
            self.assertEqual(result.tool_calls[0].id, "anthropic-native")
            self.assertIn("tools", client.messages.create.call_args.kwargs)
