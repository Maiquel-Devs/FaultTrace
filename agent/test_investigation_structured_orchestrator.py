import inspect
import json
from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase

from accounts.models import Organization, User
from assets.models import Document, Equipment
from knowledge import retrieval
from knowledge.models import DocumentSection
from maintenance.models import Incident, Investigation

from .contracts import ToolCall
from .core import MAX_TOOL_ROUNDS
from .investigation_context import SourceKind
from .investigation_context_adapter import build_authorized_investigation_context
from .investigation_contracts import StructuralContractError
from .investigation_producer_policy import (
    SAFE_VALIDATION_INSTRUCTIONS,
    ProducerFailureCategory,
    safe_validation_feedback,
)
from .investigation_structured_orchestrator import (
    DEFAULT_MAX_TOOL_ROUNDS,
    StructuredExecutionStatus,
    StructuredInvestigationOrchestrator,
)
from .investigation_structured_producer import (
    FakeStructuredProducer,
    FinalResultTurn,
    StructuredProducerError,
    ToolRequestTurn,
)
from .models import AgentInteraction


class StructuredInvestigationOrchestratorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Structured Test")
        cls.user = User.objects.create_user(
            username="structured-tech",
            password="test-password",
            organization=cls.organization,
            role=User.Role.TECHNICIAN,
        )
        cls.equipment = Equipment.objects.create(
            organization=cls.organization,
            name="Unidade estruturada",
            code="STRUCT-01",
            description="Equipamento autorizado do teste.",
        )
        cls.incident = Incident.objects.create(
            organization=cls.organization,
            equipment=cls.equipment,
            description="Falha controlada para fluxo estruturado.",
            reported_by=cls.user,
        )
        cls.investigation = Investigation.objects.create(
            incident=cls.incident,
            technician=cls.user,
        )
        cls.document = Document.objects.create(
            organization=cls.organization,
            title="Manual estruturado",
            file="documents/structured.pdf",
        )
        cls.document.equipments.add(cls.equipment)
        cls.section = DocumentSection.objects.create(
            document=cls.document,
            page_number=12,
            content="DOC-CONTENT-AUTHORIZED alarme térmico e teste recomendado.",
        )

        cls.other_equipment = Equipment.objects.create(
            organization=cls.organization,
            name="Unidade isolada",
            code="STRUCT-02",
        )
        cls.other_incident = Incident.objects.create(
            organization=cls.organization,
            equipment=cls.other_equipment,
            description="INCIDENT-CONTENT-NOT-AUTHORIZED",
            reported_by=cls.user,
        )
        Investigation.objects.create(
            incident=cls.other_incident,
            technician=cls.user,
        )
        cls.other_document = Document.objects.create(
            organization=cls.organization,
            title="Manual não autorizado",
            file="documents/not-authorized.pdf",
        )
        cls.other_document.equipments.add(cls.other_equipment)
        cls.other_section = DocumentSection.objects.create(
            document=cls.other_document,
            page_number=3,
            content="DOC-CONTENT-NOT-AUTHORIZED",
        )

    def _initial_refs(self):
        context = build_authorized_investigation_context(incident=self.incident)
        current_ref = next(
            source.ref
            for source in context.sources
            if source.kind is SourceKind.CURRENT_INCIDENT
        )
        return context, current_ref

    def _payload(self, problem_ref, *, source_ref=None, summary="Resumo válido."):
        evidence = []
        if source_ref is not None:
            evidence.append(
                {
                    "id": "evidence_1",
                    "statement": "Evidência estruturada.",
                    "significance": "Sustenta a próxima verificação.",
                    "source_refs": [source_ref],
                }
            )
        return {
            "schema_version": "1.0",
            "problem": {
                "statement": "Falha operacional sob investigação.",
                "source_refs": [problem_ref],
            },
            "summary": summary,
            "known_facts": [],
            "evidence": evidence,
            "contradictions": [],
            "hypotheses": [],
            "checks": [],
        }

    def _run(self, turns, **orchestrator_options):
        producer = FakeStructuredProducer(turns)
        execution = StructuredInvestigationOrchestrator(
            producer=producer,
            **orchestrator_options,
        ).run(
            incident=self.incident,
            request="Investigue a falha de forma estruturada.",
        )
        return execution, producer

    def test_simple_valid_result(self):
        _, current_ref = self._initial_refs()
        execution, producer = self._run(
            [FinalResultTurn(json.dumps(self._payload(current_ref)))]
        )

        self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
        self.assertIsNotNone(execution.result)
        self.assertEqual(execution.rounds, 1)
        self.assertEqual(execution.validation_attempts, 1)
        self.assertEqual(execution.retry_count, 0)
        self.assertEqual(len(producer.inputs), 1)
        self.assertEqual(execution.validation_failures, ())

    def test_validation_failure_audit_records_safe_canonical_metadata(self):
        _, current_ref = self._initial_refs()
        valid = self._payload(current_ref)

        structural = self._payload(current_ref)
        structural.pop("summary")
        reference = self._payload("src_UNTRUSTED_VALUE")
        semantic = self._payload(current_ref)
        semantic["hypotheses"] = [
            {
                "id": "hypothesis_1",
                "origin": "PROPOSED",
                "statement": "Hipótese sem relações suficientes.",
                "evidence_assessment": "MIXED",
                "supporting_finding_refs": [],
                "opposing_finding_refs": [],
                "rationale": "Rationale sintético.",
            }
        ]
        cases = (
            (
                "RAW_OUTPUT_SECRET_NOT_JSON",
                ProducerFailureCategory.SYNTAX_FAILURE,
                None,
            ),
            (
                json.dumps(structural),
                ProducerFailureCategory.STRUCTURAL_CONTRACT_FAILURE,
                "$",
            ),
            (
                json.dumps(reference),
                ProducerFailureCategory.REFERENCE_FAILURE,
                "$.problem.source_refs[0]",
            ),
            (
                json.dumps(semantic),
                ProducerFailureCategory.SEMANTIC_INVARIANT_FAILURE,
                "$.hypotheses[0]",
            ),
        )

        for invalid_raw, category, expected_path in cases:
            with self.subTest(category=category):
                execution, producer = self._run(
                    [
                        FinalResultTurn(invalid_raw),
                        FinalResultTurn(json.dumps(valid)),
                    ]
                )
                failure = execution.validation_failures[0]

                self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
                self.assertEqual(execution.retry_count, 1)
                self.assertEqual(failure.round_number, 1)
                self.assertEqual(failure.attempt, 1)
                self.assertEqual(failure.category, category)
                self.assertEqual(failure.path, expected_path)
                self.assertEqual(
                    failure.instruction,
                    SAFE_VALIDATION_INSTRUCTIONS[category],
                )
                self.assertEqual(
                    producer.inputs[1].validation_feedback,
                    {
                        "category": category.value,
                        "instruction": SAFE_VALIDATION_INSTRUCTIONS[category],
                        **({"path": expected_path} if expected_path else {}),
                    },
                )

    def test_untrusted_path_content_is_omitted_from_safe_feedback(self):
        error = StructuralContractError(
            '$.hypotheses["MODEL_SUPPLIED_SECRET"]',
            "INVALID_VALUE_SECRET",
        )

        feedback = safe_validation_feedback(error)
        serialized = json.dumps(feedback)

        self.assertEqual(
            feedback,
            {
                "category": "STRUCTURAL_CONTRACT_FAILURE",
                "instruction": SAFE_VALIDATION_INSTRUCTIONS[
                    ProducerFailureCategory.STRUCTURAL_CONTRACT_FAILURE
                ],
            },
        )
        self.assertNotIn("MODEL_SUPPLIED_SECRET", serialized)
        self.assertNotIn("INVALID_VALUE_SECRET", serialized)

    def test_validation_audit_survives_a_later_producer_failure(self):
        execution, producer = self._run(
            [
                FinalResultTurn("RAW_OUTPUT_EPHEMERAL"),
                StructuredProducerError("provider failure without raw"),
            ]
        )

        self.assertEqual(
            execution.status,
            StructuredExecutionStatus.PRODUCER_ERROR,
        )
        self.assertEqual(len(execution.validation_failures), 1)
        failure = execution.validation_failures[0]
        self.assertEqual(failure.round_number, 1)
        self.assertEqual(failure.attempt, 1)
        self.assertEqual(
            failure.category,
            ProducerFailureCategory.SYNTAX_FAILURE,
        )
        self.assertIsNone(failure.path)
        self.assertEqual(
            failure.instruction,
            SAFE_VALIDATION_INSTRUCTIONS[
                ProducerFailureCategory.SYNTAX_FAILURE
            ],
        )
        self.assertEqual(producer.inputs[1].round_number, 2)

    def test_invalid_json_gets_one_safe_retry_and_succeeds(self):
        _, current_ref = self._initial_refs()
        execution, producer = self._run(
            [
                FinalResultTurn('{"schema_version":"1.0"'),
                FinalResultTurn(json.dumps(self._payload(current_ref))),
            ]
        )

        self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
        self.assertEqual(execution.retry_count, 1)
        self.assertEqual(
            execution.validation_failures[0].category,
            ProducerFailureCategory.SYNTAX_FAILURE,
        )
        feedback = producer.inputs[1].validation_feedback
        self.assertEqual(feedback["category"], "SYNTAX_FAILURE")
        self.assertNotIn("traceback", json.dumps(feedback).lower())

    def test_structural_and_semantic_failures_are_classified(self):
        _, current_ref = self._initial_refs()
        structural = self._payload(current_ref)
        structural.pop("summary")
        semantic = self._payload(current_ref)
        semantic["hypotheses"] = [
            {
                "id": "hypothesis_1",
                "origin": "PROPOSED",
                "statement": "Hipótese inconsistente.",
                "evidence_assessment": "MIXED",
                "supporting_finding_refs": [],
                "opposing_finding_refs": [],
                "rationale": "Sem relações suficientes.",
            }
        ]
        cases = (
            (structural, ProducerFailureCategory.STRUCTURAL_CONTRACT_FAILURE),
            (semantic, ProducerFailureCategory.SEMANTIC_INVARIANT_FAILURE),
        )
        for invalid, category in cases:
            with self.subTest(category=category):
                execution, _ = self._run(
                    [
                        FinalResultTurn(json.dumps(invalid)),
                        FinalResultTurn(json.dumps(self._payload(current_ref))),
                    ]
                )
                self.assertEqual(
                    execution.status, StructuredExecutionStatus.SUCCESS
                )
                self.assertEqual(
                    execution.validation_failures[0].category, category
                )

    def test_invented_source_ref_is_retried_and_never_accepted(self):
        _, current_ref = self._initial_refs()
        invalid = self._payload("src_999")
        execution, producer = self._run(
            [
                FinalResultTurn(json.dumps(invalid)),
                FinalResultTurn(json.dumps(self._payload(current_ref))),
            ]
        )

        self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
        self.assertEqual(
            execution.validation_failures[0].category,
            ProducerFailureCategory.REFERENCE_FAILURE,
        )
        feedback = producer.inputs[1].validation_feedback
        feedback_text = json.dumps(feedback).lower()
        self.assertEqual(
            set(feedback), {"category", "instruction", "path"}
        )
        self.assertNotIn("src_999", feedback_text)
        self.assertNotIn("internal_locator", feedback_text)
        self.assertNotIn("traceback", feedback_text)

    def test_invented_registered_ref_is_reference_failure(self):
        _, current_ref = self._initial_refs()
        invalid = self._payload(current_ref)
        invalid["hypotheses"] = [
            {
                "id": "hypothesis_1",
                "origin": "REGISTERED",
                "registered_ref": "ctx_hypothesis_999",
                "statement": "Hipótese inventada.",
                "evidence_assessment": "INSUFFICIENT",
                "supporting_finding_refs": [],
                "opposing_finding_refs": [],
                "rationale": "Sem base autorizada.",
            }
        ]
        execution, _ = self._run(
            [
                FinalResultTurn(json.dumps(invalid)),
                FinalResultTurn(json.dumps(self._payload(current_ref))),
            ]
        )

        self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
        self.assertEqual(
            execution.validation_failures[0].category,
            ProducerFailureCategory.REFERENCE_FAILURE,
        )

    def test_two_invalid_attempts_end_in_controlled_failure(self):
        invalid = FinalResultTurn("not-json")
        execution, _ = self._run([invalid, invalid])

        self.assertEqual(
            execution.status, StructuredExecutionStatus.VALIDATION_FAILED
        )
        self.assertIsNone(execution.result)
        self.assertEqual(execution.validation_attempts, 2)
        self.assertEqual(len(execution.validation_failures), 2)

    def test_json_text_cannot_be_promoted_to_tool_request(self):
        turn = FinalResultTurn(
            json.dumps(
                {
                    "name": "search_documentation",
                    "arguments": {"query": "alarme"},
                }
            )
        )
        execution, _ = self._run([turn, turn])

        self.assertEqual(
            execution.status, StructuredExecutionStatus.VALIDATION_FAILED
        )
        self.assertEqual(execution.tool_calls, ())

    def test_unknown_tool_is_controlled(self):
        execution, _ = self._run(
            [
                ToolRequestTurn(
                    ToolCall(name="run_arbitrary_code", arguments={})
                )
            ]
        )

        self.assertEqual(execution.status, StructuredExecutionStatus.TOOL_ERROR)
        self.assertFalse(execution.tool_calls[0].ok)
        self.assertIsNone(execution.result)

    def test_invalid_tool_arguments_are_rejected_before_retrieval(self):
        cases = (
            ToolCall(name="search_documentation", arguments={}),
            ToolCall(name="search_documentation", arguments={"query": 7}),
            ToolCall(name="search_documentation", arguments={"query": ""}),
            ToolCall(
                name="search_documentation",
                arguments={"query": "alarme", "incident_id": 123},
            ),
            ToolCall(name="search_equipment_history", arguments={"id": 1}),
            ToolCall(name="search_similar_incidents", arguments=[]),
        )
        for call in cases:
            with self.subTest(call=call):
                with patch(
                    "agent.investigation_context_adapter.retrieval.search_documentation"
                ) as documentation:
                    execution, _ = self._run([ToolRequestTurn(call)])
                self.assertEqual(
                    execution.status, StructuredExecutionStatus.TOOL_ERROR
                )
                documentation.assert_not_called()

    def test_tool_limit_matches_legacy_policy_and_stops_loop(self):
        self.assertEqual(DEFAULT_MAX_TOOL_ROUNDS, MAX_TOOL_ROUNDS)
        turns = [
            ToolRequestTurn(
                ToolCall(name="search_equipment_history", arguments={})
            )
            for _ in range(MAX_TOOL_ROUNDS + 1)
        ]
        with patch(
            "agent.investigation_structured_tools.expand_with_equipment_history",
            wraps=__import__(
                "agent.investigation_structured_tools",
                fromlist=["expand_with_equipment_history"],
            ).expand_with_equipment_history,
        ) as expansion:
            execution, _ = self._run(turns)

        self.assertEqual(
            execution.status, StructuredExecutionStatus.TOOL_LIMIT_REACHED
        )
        self.assertEqual(expansion.call_count, MAX_TOOL_ROUNDS)
        self.assertEqual(execution.rounds, MAX_TOOL_ROUNDS + 1)
        self.assertFalse(execution.tool_calls[-1].ok)

    def test_tool_result_is_reauthorized_and_forged_section_fails(self):
        forged = retrieval.DocumentSearchResult(
            document_id=self.other_document.pk,
            document_title=self.other_document.title,
            page=self.other_section.page_number,
            content=self.other_section.content,
            reference=self.other_section.reference,
        )
        with patch(
            "agent.investigation_context_adapter.retrieval.search_documentation",
            return_value=(forged,),
        ):
            execution, _ = self._run(
                [
                    ToolRequestTurn(
                        ToolCall(
                            name="search_documentation",
                            arguments={"query": "DOC-CONTENT"},
                        )
                    )
                ]
            )

        self.assertEqual(execution.status, StructuredExecutionStatus.TOOL_ERROR)
        self.assertNotIn(
            "DOC-CONTENT-NOT-AUTHORIZED",
            json.dumps(execution.context.to_producer_dict()),
        )

    def test_tool_then_validation_retry_preserves_refs_and_executes_tool_once(self):
        initial, current_ref = self._initial_refs()
        initial_sources = initial.to_producer_dict()["sources"]
        document_ref = f"src_{len(initial.sources) + 1}"
        invalid = self._payload(current_ref, source_ref=document_ref)
        invalid.pop("summary")
        corrected = self._payload(
            current_ref,
            source_ref=document_ref,
        )
        turns = [
            ToolRequestTurn(
                ToolCall(
                    name="search_documentation",
                    arguments={"query": "alarme térmico"},
                )
            ),
            FinalResultTurn(json.dumps(invalid)),
            FinalResultTurn(json.dumps(corrected)),
        ]
        original_search = retrieval.search_documentation
        with patch(
            "agent.investigation_context_adapter.retrieval.search_documentation",
            wraps=original_search,
        ) as search:
            execution, producer = self._run(turns)

        self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
        self.assertEqual(search.call_count, 1)
        self.assertEqual(len(execution.tool_calls), 1)
        self.assertTrue(execution.tool_calls[0].ok)
        self.assertEqual(execution.retry_count, 1)
        self.assertEqual(execution.rounds, 3)
        self.assertEqual(
            producer.inputs[1].context["sources"][: len(initial_sources)],
            initial_sources,
        )
        self.assertEqual(
            producer.inputs[2].context["sources"],
            producer.inputs[1].context["sources"],
        )
        self.assertEqual(
            producer.inputs[2].validation_feedback["category"],
            "STRUCTURAL_CONTRACT_FAILURE",
        )
        self.assertEqual(
            execution.result.evidence[0].source_refs,
            (document_ref,),
        )

    def test_main_end_to_end_flow_is_isolated_read_only_and_not_persisted(self):
        before = self._domain_snapshot()
        initial, current_ref = self._initial_refs()
        document_ref = f"src_{len(initial.sources) + 1}"
        turns = [
            ToolRequestTurn(
                ToolCall(
                    name="search_documentation",
                    arguments={"query": "alarme térmico"},
                )
            ),
            FinalResultTurn(
                json.dumps(self._payload(current_ref, source_ref="src_999"))
            ),
            FinalResultTurn(
                json.dumps(self._payload(current_ref, source_ref=document_ref))
            ),
        ]

        execution, producer = self._run(turns)

        self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
        self.assertEqual(execution.rounds, 3)
        self.assertEqual(execution.validation_attempts, 2)
        self.assertEqual(len(execution.tool_calls), 1)
        self.assertEqual(
            execution.validation_failures[0].category,
            ProducerFailureCategory.REFERENCE_FAILURE,
        )
        final_context = json.dumps(
            producer.inputs[-1].context,
            ensure_ascii=False,
        )
        self.assertIn("DOC-CONTENT-AUTHORIZED", final_context)
        self.assertNotIn("DOC-CONTENT-NOT-AUTHORIZED", final_context)
        self.assertNotIn("INCIDENT-CONTENT-NOT-AUTHORIZED", final_context)
        self.assertEqual(self._domain_snapshot(), before)
        self.assertFalse(AgentInteraction.objects.exists())

    def test_producer_receives_only_explicit_provider_neutral_input(self):
        _, current_ref = self._initial_refs()
        execution, producer = self._run(
            [FinalResultTurn(json.dumps(self._payload(current_ref)))]
        )
        producer_input = producer.inputs[0]
        serialized_context = json.dumps(
            producer_input.context,
            ensure_ascii=False,
        )

        self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
        self.assertEqual(producer_input.round_number, 1)
        self.assertIn("$defs", producer_input.schema)
        self.assertEqual(
            {tool.name for tool in producer_input.tools},
            {
                "search_equipment_history",
                "search_similar_incidents",
                "search_documentation",
            },
        )
        self.assertNotIn("internal_locator", serialized_context)
        self.assertNotIn("display_label", serialized_context)
        self.assertNotIn('"pk"', serialized_context)
        self.assertNotIn('"id"', serialized_context)

    def test_security_strings_remain_untrusted_text(self):
        _, current_ref = self._initial_refs()
        unsafe = (
            "<script>alert(1)</script> "
            "<img src=x onerror=alert(1)> "
            "[link](javascript:alert(1)) **Markdown** <div>HTML</div>"
        )
        execution, _ = self._run(
            [
                FinalResultTurn(
                    json.dumps(self._payload(current_ref, summary=unsafe))
                )
            ]
        )

        self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
        self.assertEqual(execution.result.summary, unsafe)

    def test_short_minimal_and_long_rich_content_are_both_valid(self):
        _, current_ref = self._initial_refs()
        minimal = self._payload(current_ref, summary="Curto.")
        long_text = "Análise técnica extensa. " * 200
        rich = self._payload(current_ref, summary=long_text)
        rich["known_facts"] = [
            {
                "id": "fact_1",
                "statement": "Primeiro fato.",
                "source_refs": [current_ref],
            },
            {
                "id": "fact_2",
                "statement": long_text,
                "source_refs": [current_ref],
            },
        ]
        rich["evidence"] = [
            {
                "id": "evidence_1",
                "statement": "Evidência adicional.",
                "significance": long_text,
                "source_refs": [current_ref],
            }
        ]
        rich["contradictions"] = [
            {
                "id": "contradiction_1",
                "finding_refs": ["fact_1", "fact_2"],
                "explanation": "Os fatos apontam estados diferentes.",
                "implication": "Requer confirmação temporal.",
            },
            {
                "id": "contradiction_2",
                "finding_refs": ["fact_1", "evidence_1"],
                "explanation": long_text,
                "implication": "Não concluir antes da verificação.",
            },
        ]
        rich["hypotheses"] = [
            {
                "id": "hypothesis_1",
                "origin": "PROPOSED",
                "statement": "Hipótese ainda sem base suficiente.",
                "evidence_assessment": "INSUFFICIENT",
                "supporting_finding_refs": [],
                "opposing_finding_refs": [],
                "rationale": "Dados insuficientes.",
            },
            {
                "id": "hypothesis_2",
                "origin": "PROPOSED",
                "statement": long_text,
                "evidence_assessment": "MIXED",
                "supporting_finding_refs": ["fact_1"],
                "opposing_finding_refs": ["evidence_1"],
                "rationale": "Há elementos nos dois sentidos.",
            },
        ]
        rich["checks"] = [
            {
                "action": "Verificar o primeiro fato.",
                "reason": "Resolver a contradição.",
                "basis_refs": ["contradiction_1"],
                "possible_outcomes": [],
            },
            {
                "action": long_text,
                "reason": "Avaliar a segunda hipótese.",
                "basis_refs": ["hypothesis_2"],
                "possible_outcomes": [
                    {
                        "observation": "Resultado compatível.",
                        "implication": "Mantém a hipótese.",
                    },
                    {
                        "observation": "Resultado incompatível.",
                        "implication": "Enfraquece a hipótese.",
                    },
                ],
            },
        ]

        for payload in (minimal, rich):
            with self.subTest(rich=payload is rich):
                execution, _ = self._run(
                    [FinalResultTurn(json.dumps(payload))]
                )
                self.assertEqual(
                    execution.status, StructuredExecutionStatus.SUCCESS
                )

    def test_producer_failure_is_controlled(self):
        execution, _ = self._run(
            [StructuredProducerError("synthetic producer failure")]
        )

        self.assertEqual(
            execution.status, StructuredExecutionStatus.PRODUCER_ERROR
        )
        self.assertIsNone(execution.result)

    def test_new_modules_do_not_import_provider_implementations(self):
        from . import investigation_structured_orchestrator as orchestrator_module
        from . import investigation_structured_producer as producer_module
        from . import investigation_structured_tools as tools_module

        source = "\n".join(
            inspect.getsource(module)
            for module in (
                orchestrator_module,
                producer_module,
                tools_module,
            )
        ).lower()
        for brand in ("groq", "openai", "mistral", "ollama", "anthropic"):
            self.assertNotIn(brand, source)

    def _domain_snapshot(self):
        return {
            "incident": deepcopy(
                Incident.objects.filter(pk=self.incident.pk).values().get()
            ),
            "investigation": deepcopy(
                Investigation.objects.filter(pk=self.investigation.pk)
                .values()
                .get()
            ),
            "documents": Document.objects.count(),
            "sections": DocumentSection.objects.count(),
            "interactions": AgentInteraction.objects.count(),
        }
