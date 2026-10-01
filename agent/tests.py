from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import Organization, User
from assets.models import Document, Equipment
from config.models import AIConfiguration
from knowledge.models import DocumentSection, Hypothesis
from maintenance.models import Incident, Investigation

from .contracts import LLMMessage, LLMResponse, ToolCall
from .core import MAX_TOOL_ROUNDS, InvestigationAgent
from .errors import LLMProviderError
from .models import AgentInteraction
from .providers import (
    AnthropicProvider,
    GeminiProvider,
    GroqProvider,
    MistralProvider,
    OpenAIProvider,
)
from .tools import ToolContext, ToolRegistry
from .templatetags.agent_markdown import safe_markdown


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

    def test_successful_web_investigation_redirects_to_new_response(self):
        configuration = AIConfiguration(
            organization=self.organization,
            provider=AIConfiguration.Provider.GROQ,
            model="test-model",
        )
        configuration.set_api_key("test-api-key")
        configuration.save()
        provider = FakeLLMProvider([_response("Analysis complete.")])
        self.client.force_login(self.user)

        with patch("agent.core.get_active_provider", return_value=provider):
            response = self.client.post(
                reverse("agent_investigate", args=[self.incident.pk]),
                {"question": "What should we investigate?"},
            )

        self.assertRedirects(
            response,
            f"{reverse('incident_detail', args=[self.incident.pk])}#agent-response",
            fetch_redirect_response=False,
        )
        interaction = AgentInteraction.objects.get()
        self.assertEqual(interaction.investigation, self.investigation)
        self.assertEqual(interaction.question, "What should we investigate?")
        self.assertEqual(interaction.response, "Analysis complete.")

        detail = self.client.get(reverse("incident_detail", args=[self.incident.pk]))
        self.assertContains(detail, 'id="agent-response"')
        self.assertContains(detail, "Analysis complete.")
        self.assertContains(detail, 'id="investigation-assistant-form"')
        self.assertContains(detail, 'submitButton.textContent = "Investigando..."')

    def test_web_configuration_error_keeps_default_redirect(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("agent_investigate", args=[self.incident.pk]),
            {"question": "What should we investigate?"},
        )

        self.assertRedirects(
            response,
            reverse("incident_detail", args=[self.incident.pk]),
            fetch_redirect_response=False,
        )
        self.assertNotIn("#agent-response", response["Location"])
        self.assertFalse(AgentInteraction.objects.exists())

    def test_web_provider_error_keeps_safe_message_and_does_not_persist(self):
        self.client.force_login(self.user)

        with patch(
            "maintenance.views.InvestigationAgent.run",
            side_effect=LLMProviderError("technical provider detail"),
        ):
            response = self.client.post(
                reverse("agent_investigate", args=[self.incident.pk]),
                {"question": "What should we investigate?"},
                follow=True,
            )

        self.assertContains(
            response,
            "Não foi possível concluir a investigação com o provider configurado.",
        )
        self.assertNotContains(response, "technical provider detail")
        self.assertFalse(AgentInteraction.objects.exists())

    def test_markdown_is_rendered_and_model_html_is_escaped(self):
        rendered = str(
            safe_markdown(
                "### Título\n\n**Importante**\n\n1. Primeiro\n2. Segundo\n\n"
                "- Item\n\n---\n\n"
                "| Evidência | Relação |\n"
                "| --- | --- |\n"
                "| Corrente 18,2 A | Sustenta |\n\n"
                "<script>alert('xss')</script>"
                "\n\n<img src=x onerror=alert('xss')>"
                "\n\n<div onclick=alert('xss')>perigoso</div>"
                "\n\n[link perigoso](javascript:alert('xss'))"
            )
        )

        self.assertIn("<h3>Título</h3>", rendered)
        self.assertIn("<strong>Importante</strong>", rendered)
        self.assertIn("<ol>", rendered)
        self.assertIn("<ul>", rendered)
        self.assertIn("<hr", rendered)
        self.assertIn("<table>", rendered)
        self.assertIn("<th>Evidência</th>", rendered)
        self.assertIn("<th>Relação</th>", rendered)
        self.assertIn("<td>Corrente 18,2 A</td>", rendered)
        self.assertIn("<td>Sustenta</td>", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("<div onclick=", rendered)
        self.assertNotIn("<a ", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_stored_markdown_table_is_rendered_without_changing_response(self):
        stored_response = (
            "| Evidência | Relação | Observação |\n"
            "| --- | --- | --- |\n"
            "| Corrente 18,2 A | Sustenta | Acima da nominal |\n"
            "| Ventilação normal | Contradiz | Fluxo verificado |"
        )
        interaction = AgentInteraction.objects.create(
            investigation=self.investigation,
            user=self.user,
            question="Analise as evidências.",
            response=stored_response,
            provider="GROQ",
            model="stored-model",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("incident_detail", args=[self.incident.pk]))

        self.assertContains(response, "<table>")
        self.assertContains(response, "<th>Evidência</th>")
        self.assertContains(response, "<td>Corrente 18,2 A</td>")
        self.assertContains(response, "<td>Ventilação normal</td>")
        interaction.refresh_from_db()
        self.assertEqual(interaction.response, stored_response)

    def test_structured_sources_are_the_only_sources_section_rendered(self):
        provider = FakeLLMProvider(
            [
                _response(
                    tool_calls=[
                        ToolCall(
                            id="history-1",
                            name="search_equipment_history",
                            arguments={},
                        )
                    ]
                ),
                _response(
                    "### Resumo da investigação\n\nHistórico é apenas indício.\n\n"
                    "### Fontes consultadas\n\n- Fonte inventada pelo texto"
                ),
            ]
        )
        InvestigationAgent().run(
            investigation=self.investigation,
            user=self.user,
            question="Consulte o histórico.",
            provider=provider,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("incident_detail", args=[self.incident.pk]))

        self.assertContains(response, "Fontes consultadas", count=1)
        self.assertNotContains(response, "Fonte inventada pelo texto")
        self.assertContains(response, f"Ocorrência #{self.past_incident.pk}")


class SafeMarkdownTableTests(TestCase):
    def assert_table_structure(self, rendered):
        for tag in ("table", "thead", "tbody", "tr", "th", "td"):
            self.assertIn(f"<{tag}", rendered)

    def test_valid_table_with_blank_line_before_is_rendered(self):
        rendered = str(
            safe_markdown(
                "Introduction.\n\n"
                "| Item | Information |\n"
                "| --- | --- |\n"
                "| E07 | Thermal protection |"
            )
        )

        self.assert_table_structure(rendered)
        self.assertIn("<th>Item</th>", rendered)
        self.assertIn("<td>Thermal protection</td>", rendered)

    def test_valid_table_without_blank_line_matches_real_response_shape(self):
        rendered = str(
            safe_markdown(
                "**Evidencias relevantes**  \n"
                "| Tipo | Conteudo | Fonte |\n"
                "| ---- | -------- | ----- |\n"
                "| Fato | Codigo E07 | Tecnico |"
            )
        )

        self.assert_table_structure(rendered)
        self.assertIn("<strong>Evidencias relevantes</strong>", rendered)
        self.assertIn("<th>Fonte</th>", rendered)
        self.assertIn("<td>Codigo E07</td>", rendered)

    def test_lf_and_crlf_are_preserved_for_table_recognition(self):
        for line_ending in ("\n", "\r\n"):
            with self.subTest(line_ending=repr(line_ending)):
                source = line_ending.join(
                    (
                        "Previous text.",
                        "| A | B |",
                        "| --- | --- |",
                        "| 1 | 2 |",
                    )
                )

                rendered = str(safe_markdown(source))

                self.assert_table_structure(rendered)
                self.assertIn("<td>1</td>", rendered)

    def test_tables_with_two_three_and_more_columns_are_rendered(self):
        for column_count in (2, 3, 5):
            with self.subTest(column_count=column_count):
                headers = [f"H{index}" for index in range(column_count)]
                values = [f"V{index}" for index in range(column_count)]
                source = (
                    "Section\n"
                    f"| {' | '.join(headers)} |\n"
                    f"| {' | '.join(['---'] * column_count)} |\n"
                    f"| {' | '.join(values)} |"
                )

                rendered = str(safe_markdown(source))

                self.assert_table_structure(rendered)
                self.assertEqual(rendered.count("<th>"), column_count)
                self.assertEqual(rendered.count("<td>"), column_count)

    def test_plain_pipe_text_and_invalid_delimiter_do_not_become_tables(self):
        cases = (
            "Pressure | current are related readings.\nFollowing text.",
            "Introduction\n| A | B |\n| -- | invalid |\n| 1 | 2 |",
        )

        for source in cases:
            with self.subTest(source=source):
                rendered = str(safe_markdown(source))

                self.assertNotIn("<table>", rendered)

    def test_adjacent_lists_and_paragraphs_keep_their_structure(self):
        rendered = str(
            safe_markdown(
                "- First\n"
                "- Second\n\n"
                "Previous paragraph.\n"
                "| A | B |\n"
                "| --- | --- |\n"
                "| 1 | 2 |\n\n"
                "Following paragraph."
            )
        )

        self.assertIn("<ul>", rendered)
        self.assertIn("<p>Previous paragraph.</p>", rendered)
        self.assert_table_structure(rendered)
        self.assertIn("<p>Following paragraph.</p>", rendered)

    def test_security_pipeline_remains_active_with_normalized_table(self):
        rendered = str(
            safe_markdown(
                "<script>alert('xss')</script>\n\n"
                "<div onclick=alert('xss')>dangerous</div>\n\n"
                "[dangerous link](javascript:alert('xss'))\n\n"
                "Previous text.\n"
                "| A | B |\n"
                "| --- | --- |\n"
                "| 1 | 2 |"
            )
        )

        self.assert_table_structure(rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<div onclick=", rendered)
        self.assertNotIn("<a ", rendered)
        self.assertIn("&lt;script&gt;", rendered)


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


class GroqProviderTests(TestCase):
    def setUp(self):
        self.messages = [LLMMessage(role="user", content="Investigue.")]
        self.tools = ToolRegistry().definitions[:1]

    def test_tool_definition_and_structured_call_are_normalized(self):
        native_call = SimpleNamespace(
            id="groq-native",
            index=3,
            function=SimpleNamespace(
                name="get_equipment_context",
                arguments='{"equipment_id": 7}',
            ),
        )
        with patch("agent.providers.Groq") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=None, tool_calls=[native_call])
                    )
                ]
            )

            response = GroqProvider(api_key="key", model="model").generate(
                self.messages,
                tools=self.tools,
            )

        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["tools"][0]["type"], "function")
        self.assertEqual(
            request["tools"][0]["function"]["name"],
            "get_equipment_context",
        )
        self.assertEqual(response.tool_calls[0].id, "groq-native")
        self.assertEqual(response.tool_calls[0].index, 3)
        self.assertEqual(response.tool_calls[0].arguments, {"equipment_id": 7})

    def test_tool_call_and_result_are_sent_back_with_native_id(self):
        messages = [
            *self.messages,
            LLMMessage(
                role="assistant",
                tool_calls=(
                    ToolCall(
                        id="groq-native",
                        name="get_equipment_context",
                        arguments={"equipment_id": 7},
                    ),
                ),
            ),
            LLMMessage(
                role="tool",
                content='{"ok": true}',
                tool_call_id="groq-native",
                tool_name="get_equipment_context",
            ),
        ]
        with patch("agent.providers.Groq") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Contexto analisado.",
                            tool_calls=None,
                        )
                    )
                ]
            )

            response = GroqProvider(api_key="key", model="model").generate(
                messages,
                tools=self.tools,
            )

        request_messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(
            request_messages[1]["tool_calls"][0]["id"],
            "groq-native",
        )
        self.assertEqual(request_messages[2]["role"], "tool")
        self.assertEqual(request_messages[2]["tool_call_id"], "groq-native")
        self.assertEqual(response.content, "Contexto analisado.")

    def test_text_response_and_json_text_remain_plain_content(self):
        json_text = '{"name":"search_documentation","arguments":{}}'
        with patch("agent.providers.Groq") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json_text,
                            tool_calls=None,
                        )
                    )
                ]
            )

            response = GroqProvider(api_key="key", model="model").generate(
                self.messages,
            )

        self.assertEqual(response.content, json_text)
        self.assertEqual(response.tool_calls, ())

    def test_connection_is_minimal_and_does_not_send_tools(self):
        with patch("agent.providers.Groq") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="OK", tool_calls=None)
                    )
                ]
            )

            GroqProvider(api_key="key", model="model").test_connection()

        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["max_completion_tokens"], 1024)
        self.assertEqual(
            request["messages"],
            [{"role": "user", "content": "Responda apenas: OK"}],
        )
        self.assertNotIn("tools", request)
        sdk.assert_called_once_with(
            api_key="key",
            timeout=20.0,
            max_retries=0,
        )

    def test_connection_still_rejects_a_truly_empty_response(self):
        with patch("agent.providers.Groq") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="", tool_calls=None)
                    )
                ]
            )

            with self.assertLogs("agent.providers", level="ERROR") as logs:
                with self.assertRaisesRegex(LLMProviderError, "resposta vazia"):
                    GroqProvider(api_key="key", model="model").test_connection()

        output = "\n".join(logs.output)
        self.assertIn("provider=GROQ", output)
        self.assertIn("model=model", output)
        self.assertIn("type=LLMProviderError", output)
        self.assertIn("Groq retornou uma resposta vazia.", output)

    def test_invalid_tool_arguments_are_logged_without_payload(self):
        invalid_payload = "not-json-sensitive-payload"
        native_call = SimpleNamespace(
            id="groq-native",
            index=0,
            function=SimpleNamespace(
                name="search_documentation",
                arguments=invalid_payload,
            ),
        )
        with patch("agent.providers.Groq") as sdk:
            client = sdk.return_value.__enter__.return_value
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=None, tool_calls=[native_call])
                    )
                ]
            )

            with self.assertLogs("agent.providers", level="ERROR") as logs:
                with self.assertRaisesRegex(LLMProviderError, "Tool inválidos"):
                    GroqProvider(api_key="key", model="model").generate(self.messages)

        output = "\n".join(logs.output)
        self.assertIn("type=LLMProviderError", output)
        self.assertIn("argumentos de Tool inválidos", output)
        self.assertNotIn(invalid_payload, output)

    def test_sdk_failures_log_safe_technical_metadata(self):
        secret = "groq-secret-key"
        prompt = self.messages[0].content
        cases = (
            ("APITimeoutError", None, None),
            ("RateLimitError", 429, "request-rate-limit"),
            ("BadRequestError", 400, "request-bad-request"),
        )

        for exception_name, status_code, request_id in cases:
            with self.subTest(exception_name=exception_name):
                exception_class = type(exception_name, (Exception,), {})
                error = exception_class(f"sensitive failure: {secret}; {prompt}")
                error.status_code = status_code
                error.request_id = request_id
                provider = GroqProvider(api_key=secret, model="test-model")

                with patch("agent.providers.Groq", side_effect=error):
                    with self.assertLogs("agent.providers", level="ERROR") as logs:
                        with self.assertRaises(LLMProviderError):
                            provider.generate(self.messages)

                output = "\n".join(logs.output)
                self.assertIn("provider=GROQ", output)
                self.assertIn("model=test-model", output)
                self.assertIn(f"type={exception_name}", output)
                self.assertIn(f"status={status_code}", output)
                self.assertIn(f"request_id={request_id}", output)
                self.assertNotIn(secret, output)
                self.assertNotIn(prompt, output)

    def test_sdk_error_is_controlled_and_does_not_expose_api_key(self):
        secret = "groq-secret-key"
        provider = GroqProvider(api_key=secret, model="model")
        with patch(
            "agent.providers.Groq",
            side_effect=RuntimeError(f"authorization failed: {secret}"),
        ):
            with self.assertLogs("agent.providers", level="ERROR") as logs:
                with self.assertRaises(LLMProviderError) as captured:
                    provider.generate(self.messages)

        output = "\n".join(logs.output)
        self.assertNotIn(secret, str(captured.exception))
        self.assertNotIn(secret, repr(captured.exception))
        self.assertNotIn(secret, output)
        self.assertIsNone(captured.exception.__cause__)
