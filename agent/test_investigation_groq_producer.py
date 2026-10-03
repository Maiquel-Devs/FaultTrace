import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase

from accounts.models import Organization, User
from assets.models import Document, Equipment
from knowledge.models import DocumentSection
from maintenance.models import Incident, Investigation

from .contracts import ToolCall
from .investigation_context import SourceKind
from .investigation_context_adapter import build_authorized_investigation_context
from .investigation_groq_producer import (
    GroqStructuredInvestigationProducer,
    GroqStructuredResponseError,
    GroqTransportFailure,
)
from .investigation_producer_policy import (
    ProducerFailureCategory,
    RetryDisposition,
)
from .investigation_schema import investigation_result_v1_schema
from .investigation_structured_orchestrator import (
    StructuredExecutionStatus,
    StructuredInvestigationOrchestrator,
)
from .investigation_structured_producer import (
    FinalResultTurn,
    StructuredProducerInput,
    ToolRequestTurn,
)
from .investigation_structured_tools import StructuredInvestigationToolRegistry
from .models import AgentInteraction


def _response(*, content=None, tool_calls=(), usage=True, reasoning=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    tool_calls=list(tool_calls),
                    reasoning=reasoning,
                )
            )
        ],
        usage=(
            SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
            )
            if usage
            else None
        ),
    )


def _native_tool(name, arguments, *, call_id="groq-call-1", index=0):
    return SimpleNamespace(
        id=call_id,
        index=index,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _provider_error(name, *, status=None, request_id=None, retry_after=None):
    error_class = type(name, (Exception,), {})
    error = error_class("sensitive provider body with secret-api-key")
    error.status_code = status
    error.request_id = request_id
    error.response = SimpleNamespace(
        headers=(
            {"retry-after": str(retry_after)}
            if retry_after is not None
            else {}
        )
    )
    return error


class GroqStructuredProducerAdapterTests(TestCase):
    def setUp(self):
        self.tools = StructuredInvestigationToolRegistry().definitions
        self.producer_input = StructuredProducerInput(
            request="Investigue a ocorrência sintética.",
            context={
                "sources": [
                    {
                        "ref": "src_1",
                        "kind": "CURRENT_INCIDENT",
                        "content": '{"description":"Falha sintética"}',
                    }
                ],
                "registered_hypotheses": [],
            },
            schema=investigation_result_v1_schema(),
            tools=self.tools,
            round_number=1,
        )

    def _producer(self, **options):
        return GroqStructuredInvestigationProducer(
            api_key="secret-api-key",
            model="openai/gpt-oss-120b",
            sleeper=options.pop("sleeper", Mock()),
            **options,
        )

    def _sdk(self, *, response=None, side_effect=None):
        patcher = patch("agent.investigation_groq_producer.Groq")
        sdk = patcher.start()
        self.addCleanup(patcher.stop)
        create = sdk.return_value.__enter__.return_value.chat.completions.create
        if side_effect is not None:
            create.side_effect = side_effect
        else:
            create.return_value = response
        return sdk, create

    def test_final_content_remains_raw_and_unvalidated_by_adapter(self):
        raw = '{"schema_version":"1.0"}'
        _, create = self._sdk(response=_response(content=raw))
        producer = self._producer()

        turn = producer.produce(self.producer_input)

        self.assertIsInstance(turn, FinalResultTurn)
        self.assertEqual(turn.raw_output, raw)
        request = create.call_args.kwargs
        self.assertNotIn("response_format", request)
        self.assertFalse(request["parallel_tool_calls"])
        self.assertEqual(request["tool_choice"], "auto")
        self.assertEqual(producer.last_usage.total_tokens, 150)

    def test_input_schema_context_and_tools_are_translated_without_internal_data(self):
        _, create = self._sdk(response=_response(content="{}"))
        producer = self._producer()

        producer.produce(self.producer_input)

        request = create.call_args.kwargs
        messages = request["messages"]
        user_payload = json.loads(messages[-1]["content"])
        self.assertEqual(
            set(user_payload),
            {
                "objective",
                "authorized_context",
                "investigation_result_schema",
                "validation_feedback",
            },
        )
        self.assertEqual(
            user_payload["investigation_result_schema"],
            investigation_result_v1_schema(),
        )
        serialized = json.dumps(request, ensure_ascii=False)
        self.assertNotIn("internal_locator", serialized)
        self.assertNotIn("display_label", serialized)
        self.assertNotIn('"pk"', serialized)
        self.assertEqual(
            {item["function"]["name"] for item in request["tools"]},
            {
                "search_equipment_history",
                "search_similar_incidents",
                "search_documentation",
            },
        )
        for tool in request["tools"]:
            properties = tool["function"]["parameters"]["properties"]
            self.assertFalse(
                {"incident_id", "equipment_id", "organization_id", "document_id"}
                & set(properties)
            )

    def test_json_mode_is_used_only_when_tool_use_is_not_enabled(self):
        _, create = self._sdk(response=_response(content="{}"))
        no_tools = StructuredProducerInput(
            request=self.producer_input.request,
            context=self.producer_input.context,
            schema=self.producer_input.schema,
            tools=(),
            round_number=1,
        )

        self._producer().produce(no_tools)

        request = create.call_args.kwargs
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)

    def test_native_tool_call_preserves_id_and_index(self):
        native = _native_tool(
            "search_documentation",
            '{"query":"alarme térmico"}',
            call_id="native-groq-id",
            index=4,
        )
        self._sdk(response=_response(tool_calls=(native,)))
        producer = self._producer()

        turn = producer.produce(self.producer_input)

        self.assertIsInstance(turn, ToolRequestTurn)
        self.assertEqual(turn.tool_call.id, "native-groq-id")
        self.assertEqual(turn.tool_call.index, 4)
        self.assertEqual(
            turn.tool_call.arguments, {"query": "alarme térmico"}
        )

    def test_unknown_and_invalid_tool_calls_stay_explicit(self):
        cases = (
            _native_tool("unknown_tool", "{}"),
            _native_tool("search_documentation", '{"incident_id":123}'),
        )
        for native in cases:
            with self.subTest(name=native.function.name):
                self._sdk(response=_response(tool_calls=(native,)))
                turn = self._producer().produce(self.producer_input)
                self.assertIsInstance(turn, ToolRequestTurn)

    def test_text_that_looks_like_tool_is_final_content(self):
        content = '{"name":"search_documentation","arguments":{"query":"x"}}'
        self._sdk(response=_response(content=content))

        turn = self._producer().produce(self.producer_input)

        self.assertIsInstance(turn, FinalResultTurn)
        self.assertEqual(turn.raw_output, content)

    def test_empty_or_unexpected_sdk_response_is_rejected(self):
        cases = (
            _response(content=None),
            SimpleNamespace(choices=[]),
            SimpleNamespace(choices=[SimpleNamespace(message=None)]),
        )
        for response in cases:
            with self.subTest(response=response):
                self._sdk(response=response)
                with self.assertRaises(GroqStructuredResponseError):
                    self._producer().produce(self.producer_input)

    def test_multiple_tool_calls_are_rejected_without_discarding_silently(self):
        calls = (
            _native_tool("search_equipment_history", "{}", call_id="call-1"),
            _native_tool(
                "search_documentation",
                '{"query":"alarm"}',
                call_id="call-2",
                index=1,
            ),
        )
        self._sdk(response=_response(tool_calls=calls))

        with self.assertRaisesRegex(
            GroqStructuredResponseError, "multiple Tool calls"
        ):
            self._producer().produce(self.producer_input)

    def test_tool_call_requires_native_id_but_index_is_optional(self):
        missing_id = _native_tool(
            "search_equipment_history", "{}", call_id=None
        )
        self._sdk(response=_response(tool_calls=(missing_id,)))
        with self.assertRaisesRegex(GroqStructuredResponseError, "native id"):
            self._producer().produce(self.producer_input)

        no_index = _native_tool(
            "search_equipment_history", "{}", index=None
        )
        self._sdk(response=_response(tool_calls=(no_index,)))
        turn = self._producer().produce(self.producer_input)
        self.assertIsNone(turn.tool_call.index)

    def test_tool_protocol_continuity_uses_native_id_on_next_round(self):
        native = _native_tool(
            "search_documentation",
            '{"query":"alarm"}',
            call_id="native-roundtrip-id",
        )
        _, create = self._sdk(
            side_effect=[
                _response(tool_calls=(native,)),
                _response(content='{"schema_version":"1.0"}'),
            ]
        )
        producer = self._producer()

        first = producer.produce(self.producer_input)
        second_input = StructuredProducerInput(
            request=self.producer_input.request,
            context=self.producer_input.context,
            schema=self.producer_input.schema,
            tools=self.producer_input.tools,
            round_number=2,
        )
        second = producer.produce(second_input)

        self.assertIsInstance(first, ToolRequestTurn)
        self.assertIsInstance(second, FinalResultTurn)
        messages = create.call_args_list[1].kwargs["messages"]
        assistant = next(item for item in messages if item["role"] == "assistant")
        tool_result = next(item for item in messages if item["role"] == "tool")
        self.assertEqual(
            assistant["tool_calls"][0]["id"], "native-roundtrip-id"
        )
        self.assertEqual(tool_result["tool_call_id"], "native-roundtrip-id")

    def test_tool_protocol_continuity_preserves_gpt_oss_reasoning_field(self):
        native = _native_tool(
            "search_documentation",
            '{"query":"alarme térmico"}',
            call_id="native-reasoning-id",
        )
        _, create = self._sdk(
            response=_response(
                tool_calls=(native,),
                reasoning="provider reasoning required for continuation",
            )
        )
        producer = self._producer()

        producer.produce(self.producer_input)
        producer.produce(
            StructuredProducerInput(
                request=self.producer_input.request,
                context=self.producer_input.context,
                schema=self.producer_input.schema,
                tools=self.producer_input.tools,
                round_number=2,
            )
        )

        messages = create.call_args_list[1].kwargs["messages"]
        assistant = next(item for item in messages if item["role"] == "assistant")
        self.assertEqual(
            assistant["reasoning"],
            "provider reasoning required for continuation",
        )

    def test_validation_feedback_is_sent_without_previous_raw_output(self):
        invalid_raw = "sensitive-invalid-raw-output"
        _, create = self._sdk(
            side_effect=[
                _response(content=invalid_raw),
                _response(content="{}"),
            ]
        )
        producer = self._producer()
        producer.produce(self.producer_input)
        correction = StructuredProducerInput(
            request=self.producer_input.request,
            context=self.producer_input.context,
            schema=self.producer_input.schema,
            tools=self.producer_input.tools,
            round_number=2,
            validation_feedback={
                "category": "SYNTAX_FAILURE",
                "instruction": "Return one complete JSON object.",
            },
        )

        producer.produce(correction)

        request_text = json.dumps(
            create.call_args_list[1].kwargs,
            ensure_ascii=False,
        )
        self.assertIn("SYNTAX_FAILURE", request_text)
        self.assertNotIn(invalid_raw, request_text)

    def test_retryable_transport_failures_retry_once(self):
        cases = (
            _provider_error("APITimeoutError"),
            _provider_error(
                "RateLimitError",
                status=429,
                request_id="request-rate",
                retry_after=0.25,
            ),
            _provider_error(
                "InternalServerError", status=500, request_id="request-server"
            ),
        )
        for error in cases:
            with self.subTest(error=type(error).__name__):
                _, create = self._sdk(
                    side_effect=[error, _response(content="{}")]
                )
                sleeper = Mock()
                producer = self._producer(sleeper=sleeper)

                turn = producer.produce(self.producer_input)

                self.assertIsInstance(turn, FinalResultTurn)
                self.assertEqual(create.call_count, 2)
                self.assertEqual(producer.transport_retries, 1)
                if error.status_code == 429:
                    sleeper.assert_called_once_with(0.25)

    def test_non_retryable_4xx_failures_are_safe(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                error = _provider_error(
                    "APIStatusError",
                    status=status,
                    request_id=f"request-{status}",
                )
                _, create = self._sdk(side_effect=error)
                with self.assertLogs(
                    "agent.investigation_groq_producer", level="ERROR"
                ) as logs:
                    with self.assertRaises(GroqTransportFailure) as captured:
                        self._producer().produce(self.producer_input)

                failure = captured.exception
                self.assertEqual(
                    failure.category,
                    ProducerFailureCategory.TRANSPORT_PROVIDER_FAILURE,
                )
                self.assertEqual(
                    failure.retryability, RetryDisposition.NON_RETRYABLE
                )
                self.assertEqual(failure.status_code, status)
                self.assertEqual(create.call_count, 1)
                output = "\n".join(logs.output)
                self.assertNotIn("secret-api-key", output)
                self.assertNotIn(self.producer_input.request, output)
                self.assertNotIn("Falha sintética", output)

    def test_repeated_transport_failure_obeys_global_retry_budget(self):
        first = _provider_error("APITimeoutError")
        second = _provider_error("InternalServerError", status=500)
        _, create = self._sdk(side_effect=[first, second])
        producer = self._producer()

        with self.assertRaises(GroqTransportFailure) as captured:
            producer.produce(self.producer_input)

        self.assertEqual(create.call_count, 2)
        self.assertEqual(producer.transport_retries, 1)
        self.assertEqual(captured.exception.failure_type, "SERVER_ERROR")


class GroqStructuredOrchestratorIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Groq Mock Test")
        cls.user = User.objects.create_user(
            username="groq-structured-tech",
            password="test-password",
            organization=cls.organization,
            role=User.Role.TECHNICIAN,
        )
        cls.equipment = Equipment.objects.create(
            organization=cls.organization,
            name="Equipamento sintético",
            code="GROQ-MOCK-01",
        )
        cls.incident = Incident.objects.create(
            organization=cls.organization,
            equipment=cls.equipment,
            description="Falha térmica sintética.",
            reported_by=cls.user,
        )
        cls.investigation = Investigation.objects.create(
            incident=cls.incident,
            technician=cls.user,
        )
        cls.document = Document.objects.create(
            organization=cls.organization,
            title="Manual fictício",
            file="documents/groq-mock.pdf",
        )
        cls.document.equipments.add(cls.equipment)
        cls.section = DocumentSection.objects.create(
            document=cls.document,
            page_number=4,
            content="THERMAL-MOCK-DOCUMENT procedimento de inspeção.",
        )

    def _refs(self):
        context = build_authorized_investigation_context(incident=self.incident)
        current_ref = next(
            item.ref
            for item in context.sources
            if item.kind is SourceKind.CURRENT_INCIDENT
        )
        document_ref = f"src_{len(context.sources) + 1}"
        return current_ref, document_ref

    def _payload(self, problem_ref, source_ref=None):
        return {
            "schema_version": "1.0",
            "problem": {
                "statement": "Falha térmica sintética.",
                "source_refs": [problem_ref],
            },
            "summary": "A documentação orienta inspeção sem confirmar causa.",
            "known_facts": [],
            "evidence": (
                [
                    {
                        "id": "evidence_1",
                        "statement": "O manual contém procedimento aplicável.",
                        "significance": "Orienta verificação técnica.",
                        "source_refs": [source_ref],
                    }
                ]
                if source_ref
                else []
            ),
            "contradictions": [],
            "hypotheses": [],
            "checks": [],
        }

    def _run_with_responses(self, responses):
        with patch("agent.investigation_groq_producer.Groq") as sdk:
            create = (
                sdk.return_value.__enter__.return_value.chat.completions.create
            )
            create.side_effect = responses
            producer = GroqStructuredInvestigationProducer(
                api_key="mock-secret-key",
                model="openai/gpt-oss-120b",
                sleeper=Mock(),
            )
            execution = StructuredInvestigationOrchestrator(
                producer=producer
            ).run(
                incident=self.incident,
                request="Investigue usando documentação quando necessário.",
            )
        return execution, producer, create

    def test_mocked_groq_tool_roundtrip_reaches_valid_contract(self):
        current_ref, document_ref = self._refs()
        tool = _native_tool(
            "search_documentation",
            '{"query":"THERMAL-MOCK-DOCUMENT"}',
            call_id="groq-tool-roundtrip",
        )
        final = self._payload(current_ref, document_ref)

        execution, producer, create = self._run_with_responses(
            [
                _response(tool_calls=(tool,)),
                _response(content=json.dumps(final, ensure_ascii=False)),
            ]
        )

        self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
        self.assertEqual(execution.rounds, 2)
        self.assertEqual(len(execution.tool_calls), 1)
        self.assertEqual(execution.result.evidence[0].source_refs, (document_ref,))
        self.assertEqual(create.call_count, 2)
        self.assertEqual(producer.transport_retries, 0)
        self.assertFalse(AgentInteraction.objects.exists())
        second_request = create.call_args_list[1].kwargs
        self.assertEqual(
            next(
                message
                for message in second_request["messages"]
                if message["role"] == "tool"
            )["tool_call_id"],
            "groq-tool-roundtrip",
        )

    def test_mocked_validation_failures_use_existing_orchestrator_retry(self):
        current_ref, _ = self._refs()
        valid = self._payload(current_ref)
        cases = (
            ("{", ProducerFailureCategory.SYNTAX_FAILURE),
            (
                json.dumps({"schema_version": "1.0"}),
                ProducerFailureCategory.STRUCTURAL_CONTRACT_FAILURE,
            ),
            (
                json.dumps(self._payload("src_999")),
                ProducerFailureCategory.REFERENCE_FAILURE,
            ),
        )
        for invalid, category in cases:
            with self.subTest(category=category):
                execution, _, create = self._run_with_responses(
                    [
                        _response(content=invalid),
                        _response(content=json.dumps(valid)),
                    ]
                )
                self.assertEqual(
                    execution.status, StructuredExecutionStatus.SUCCESS
                )
                self.assertEqual(create.call_count, 2)
                self.assertEqual(execution.retry_count, 1)
                self.assertEqual(
                    execution.validation_failures[0].category, category
                )

    def test_unknown_or_invalid_native_tool_is_stopped_by_allow_list(self):
        calls = (
            _native_tool("unknown_tool", "{}"),
            _native_tool("search_documentation", '{"incident_id":123}'),
        )
        for call in calls:
            with self.subTest(name=call.function.name):
                execution, _, create = self._run_with_responses(
                    [_response(tool_calls=(call,))]
                )
                self.assertEqual(
                    execution.status, StructuredExecutionStatus.TOOL_ERROR
                )
                self.assertEqual(create.call_count, 1)

    def test_transport_retry_is_separate_from_validation_retry(self):
        current_ref, _ = self._refs()
        valid = self._payload(current_ref)
        timeout = _provider_error("APITimeoutError")
        execution, producer, create = self._run_with_responses(
            [timeout, _response(content=json.dumps(valid))]
        )

        self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
        self.assertEqual(execution.validation_attempts, 1)
        self.assertEqual(execution.retry_count, 0)
        self.assertEqual(producer.transport_retries, 1)
        self.assertEqual(create.call_count, 2)
