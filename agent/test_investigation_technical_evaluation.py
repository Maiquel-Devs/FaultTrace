import json
from dataclasses import replace

from django.test import TestCase

from .investigation_context import SourceCandidate, SourceKind, build_investigation_context
from .investigation_contracts import ProblemStatement, parse_investigation_result
from .investigation_groq_producer import _producer_input_json
from .investigation_schema import investigation_result_v1_schema
from .investigation_structured_orchestrator import (
    StructuredExecutionStatus,
    StructuredInvestigationOrchestrator,
    ValidationFailureAudit,
)
from .investigation_structured_producer import (
    FakeStructuredProducer,
    FinalResultTurn,
    StructuredProducerInput,
)
from .investigation_structured_tools import StructuredInvestigationToolRegistry
from .investigation_technical_corpus import (
    TECHNICAL_EVALUATION_CASES,
    get_technical_evaluation_cases,
)
from .investigation_technical_evaluation import EvaluationVerdict, evaluate_automatic
from .models import AgentInteraction
from .phase41_experiment import (
    _validation_failure_record,
    materialize_case,
)
from .investigation_producer_policy import ProducerFailureCategory


def _context_for(case):
    sources = [
        SourceCandidate(
            internal_locator="incident:current",
            kind=SourceKind.CURRENT_INCIDENT,
            authorized_content=json.dumps(
                {"description": case.model_input.incident_description}
            ),
            display_label="Ocorrência sintética",
        )
    ]
    for fixture in case.model_input.facts:
        sources.append(
            SourceCandidate(
                internal_locator=fixture.locator,
                kind=SourceKind.FACT,
                authorized_content=json.dumps({"content": fixture.content}),
                display_label="Fato sintético",
            )
        )
    for fixture in case.model_input.evidence:
        sources.append(
            SourceCandidate(
                internal_locator=fixture.locator,
                kind=SourceKind.EVIDENCE,
                authorized_content=json.dumps({"content": fixture.content}),
                display_label="Evidência sintética",
            )
        )
    for fixture in case.model_input.documents:
        sources.append(
            SourceCandidate(
                internal_locator=fixture.locator,
                kind=SourceKind.DOCUMENT_SECTION,
                authorized_content=json.dumps({"content": fixture.content}),
                display_label="Documento sintético",
            )
        )
    return build_investigation_context(sources=sources)


def _minimal_payload(ref):
    return {
        "schema_version": "1.0",
        "problem": {"statement": "Problema sob investigação.", "source_refs": [ref]},
        "summary": "Dados preservados sem confirmação de causa.",
        "known_facts": [],
        "evidence": [],
        "contradictions": [],
        "hypotheses": [],
        "checks": [],
    }


class TechnicalCorpusUnitTests(TestCase):
    def test_phase41_validation_telemetry_contains_only_safe_metadata(self):
        failure = ValidationFailureAudit(
            round_number=2,
            attempt=1,
            category=ProducerFailureCategory.REFERENCE_FAILURE,
            path="$.hypotheses[0].supporting_finding_refs[0]",
            instruction=(
                "Use only references present in the supplied authorized context."
            ),
        )

        record = _validation_failure_record(failure)
        serialized = json.dumps(record)

        self.assertEqual(
            record,
            {
                "round_number": 2,
                "attempt": 1,
                "category": "REFERENCE_FAILURE",
                "path": "$.hypotheses[0].supporting_finding_refs[0]",
                "instruction": (
                    "Use only references present in the supplied authorized context."
                ),
            },
        )
        for forbidden in (
            "raw_output",
            "INVALID_VALUE",
            "prompt",
            "CONTEXT_SECRET",
            "messages",
            "reasoning",
            "source_content",
            "tool_arguments",
            "incident",
            "traceback",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_fixtures_load_and_cover_adversarial_cases_and_domains(self):
        cases = get_technical_evaluation_cases()

        self.assertEqual([item.case_id for item in cases], ["T1", "T2", "T3", "T4", "T5"])
        self.assertEqual(len({item.model_input.domain for item in cases}), 5)
        self.assertGreaterEqual(cases[1].expectations.minimum_contradictions, 1)
        self.assertIn("insuficiente", cases[2].title.casefold())
        self.assertIn("histórico", cases[3].title.casefold())
        self.assertTrue(cases[4].model_input.documents)

    def test_expectations_never_enter_producer_input(self):
        case = TECHNICAL_EVALUATION_CASES[0]
        context = _context_for(case)
        producer_input = StructuredProducerInput(
            request=case.model_input.request,
            context=context.to_producer_dict(),
            schema=investigation_result_v1_schema(),
            tools=StructuredInvestigationToolRegistry().definitions,
            round_number=1,
        )

        serialized = _producer_input_json(producer_input)

        self.assertNotIn("expected_reasoning_properties", serialized)
        self.assertNotIn("must_not_assert", serialized)
        for evaluator_only_text in case.expectations.expected_reasoning_properties:
            self.assertNotIn(evaluator_only_text, serialized)

    def test_each_case_context_can_validate_an_investigation_result_v1(self):
        for case in TECHNICAL_EVALUATION_CASES:
            with self.subTest(case=case.case_id):
                context = _context_for(case)
                payload = _minimal_payload(context.sources[0].ref)
                result = parse_investigation_result(
                    payload,
                    source_catalog=context.source_catalog,
                    registered_hypothesis_catalog=context.registered_hypothesis_catalog,
                )
                self.assertEqual(result.schema_version, "1.0")

    def test_evaluator_rejects_invented_refs_even_for_direct_dataclass(self):
        case = TECHNICAL_EVALUATION_CASES[0]
        context = _context_for(case)
        result = parse_investigation_result(
            _minimal_payload(context.sources[0].ref),
            source_catalog=context.source_catalog,
            registered_hypothesis_catalog=context.registered_hypothesis_catalog,
        )
        forged = replace(
            result,
            problem=ProblemStatement("Problema forjado.", ("src_999",)),
        )

        evaluation = evaluate_automatic(case, context, forged)

        self.assertEqual(evaluation.refs.verdict, EvaluationVerdict.FAIL)

    def test_proposed_hypothesis_is_not_automatically_an_invented_fact(self):
        case = TECHNICAL_EVALUATION_CASES[2]
        context = _context_for(case)
        fact_ref = next(
            source.ref for source in context.sources if source.kind is SourceKind.FACT
        )
        payload = _minimal_payload(context.sources[0].ref)
        payload["known_facts"] = [
            {
                "id": "fact_1",
                "statement": case.model_input.facts[0].content,
                "source_refs": [fact_ref],
            }
        ]
        payload["hypotheses"] = [
            {
                "id": "hypothesis_1",
                "origin": "PROPOSED",
                "statement": "Mau contato intermitente no circuito de entrada.",
                "evidence_assessment": "INSUFFICIENT",
                "supporting_finding_refs": [],
                "opposing_finding_refs": [],
                "rationale": "É uma possibilidade ainda sem evidência suficiente.",
            }
        ]
        payload["checks"] = [
            {
                "action": "Medir a tensão no circuito de entrada.",
                "reason": "Coletar o dado ausente.",
                "basis_refs": ["hypothesis_1"],
                "possible_outcomes": [],
            }
        ]
        result = parse_investigation_result(
            payload,
            source_catalog=context.source_catalog,
            registered_hypothesis_catalog=context.registered_hypothesis_catalog,
        )

        evaluation = evaluate_automatic(case, context, result)

        self.assertEqual(evaluation.hypotheses.verdict, EvaluationVerdict.PASS)
        self.assertFalse(evaluation.invented_facts_detected)

    def test_evaluation_does_not_mutate_result(self):
        case = TECHNICAL_EVALUATION_CASES[0]
        context = _context_for(case)
        result = parse_investigation_result(
            _minimal_payload(context.sources[0].ref),
            source_catalog=context.source_catalog,
            registered_hypothesis_catalog=context.registered_hypothesis_catalog,
        )
        before = result.to_dict()

        evaluate_automatic(case, context, result)

        self.assertEqual(result.to_dict(), before)


class TechnicalCorpusIntegrationTests(TestCase):
    def test_all_case_fixtures_materialize_into_authorized_contexts(self):
        from .investigation_context_adapter import build_authorized_investigation_context

        for case in TECHNICAL_EVALUATION_CASES:
            with self.subTest(case=case.case_id):
                materialized = materialize_case(case)
                context = build_authorized_investigation_context(
                    incident=materialized.incident
                )
                self.assertTrue(context.sources)
                self.assertEqual(
                    len(context.registered_hypotheses),
                    len(case.model_input.hypotheses),
                )

    def test_corpus_path_is_provider_neutral_and_does_not_persist_raw_output(self):
        case = TECHNICAL_EVALUATION_CASES[0]
        materialized = materialize_case(case)
        initial_interactions = AgentInteraction.objects.count()
        # The first source ref is discovered through the same adapter used by the
        # orchestrator, so this test does not depend on database primary keys.
        from .investigation_context_adapter import build_authorized_investigation_context

        context = build_authorized_investigation_context(incident=materialized.incident)
        payload = _minimal_payload(context.sources[0].ref)
        producer = FakeStructuredProducer([FinalResultTurn(json.dumps(payload))])

        execution = StructuredInvestigationOrchestrator(producer=producer).run(
            incident=materialized.incident,
            request=case.model_input.request,
        )

        self.assertEqual(execution.status, StructuredExecutionStatus.SUCCESS)
        self.assertEqual(AgentInteraction.objects.count(), initial_interactions)
        self.assertIsInstance(producer, FakeStructuredProducer)
