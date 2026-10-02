import json
from copy import deepcopy
from dataclasses import fields
from unittest import TestCase

from .investigation_context import (
    ContextBuildError,
    RegisteredHypothesisCandidate,
    SourceCandidate,
    SourceKind,
    build_investigation_context,
)
from .investigation_contracts import (
    ContractParseError,
    EvidenceAssessment,
    HypothesisOrigin,
    ReferenceContractError,
    SemanticInvariantError,
    StructuralContractError,
    TOP_LEVEL_FIELDS,
    parse_investigation_result,
    parse_investigation_result_json,
)
from .investigation_producer_policy import (
    ProducerFailureCategory,
    RetryDisposition,
    classify_contract_exception,
    recommended_retry,
    safe_validation_feedback,
)
from .investigation_schema import investigation_result_v1_schema
from .testdata.investigation_context_fixtures import (
    REALISTIC_CONTEXT,
    REGISTERED_HYPOTHESIS_CANDIDATES,
    SIMULATED_RESULT_JSON,
    SOURCE_CANDIDATES,
    SOURCE_REFS_BY_LOCATOR,
    simulated_payload,
)
from .testdata.investigation_contract_fixtures import CORPUS_A_TO_L


def _parse(payload):
    return parse_investigation_result(
        payload,
        source_catalog=REALISTIC_CONTEXT.source_catalog,
        registered_hypothesis_catalog=(
            REALISTIC_CONTEXT.registered_hypothesis_catalog
        ),
    )


class InvestigationContextPipelineTests(TestCase):
    def test_synthetic_realistic_context_reaches_valid_contract(self):
        result = parse_investigation_result_json(
            SIMULATED_RESULT_JSON,
            source_catalog=REALISTIC_CONTEXT.source_catalog,
            registered_hypothesis_catalog=(
                REALISTIC_CONTEXT.registered_hypothesis_catalog
            ),
        )

        self.assertEqual(result.schema_version, "1.0")
        self.assertEqual(result.hypotheses[0].origin, HypothesisOrigin.REGISTERED)
        self.assertEqual(
            result.hypotheses[0].evidence_assessment,
            EvidenceAssessment.MIXED,
        )

    def test_unknown_source_from_simulated_producer_is_rejected(self):
        payload = simulated_payload()
        payload["problem"]["source_refs"] = ["src_not_authorized"]

        with self.assertRaises(ReferenceContractError):
            _parse(payload)

    def test_unknown_registered_hypothesis_is_rejected(self):
        payload = simulated_payload()
        payload["hypotheses"][0]["registered_ref"] = "ctx_hypothesis_missing"

        with self.assertRaises(ReferenceContractError):
            _parse(payload)

    def test_payload_cannot_define_its_own_catalog(self):
        payload = simulated_payload()
        payload["sources"] = [
            {"ref": "src_invented", "kind": "DOCUMENT", "content": "invented"}
        ]

        with self.assertRaises(StructuralContractError):
            _parse(payload)


class InvestigationContextCatalogTests(TestCase):
    def test_source_kinds_match_current_domain_and_retrieval_shapes(self):
        self.assertEqual(
            {item.value for item in SourceKind},
            {
                "CURRENT_INCIDENT",
                "EQUIPMENT",
                "INVESTIGATION",
                "FACT",
                "EVIDENCE",
                "HISTORICAL_INCIDENT",
                "INTERVENTION",
                "DOCUMENT",
                "DOCUMENT_SECTION",
            },
        )

    def test_context_builds_existing_validation_catalogs(self):
        self.assertEqual(
            REALISTIC_CONTEXT.source_catalog.refs,
            frozenset(REALISTIC_CONTEXT.consulted_source_refs),
        )
        self.assertEqual(
            REALISTIC_CONTEXT.registered_hypothesis_catalog.refs,
            frozenset({"ctx_hypothesis_1"}),
        )

    def test_registered_hypothesis_snapshot_preserves_read_only_state(self):
        snapshot = REALISTIC_CONTEXT.registered_hypotheses[0]

        self.assertEqual(snapshot.observed_status, "ACTIVE")
        self.assertTrue(snapshot.statement)
        self.assertEqual(len(snapshot.supporting_source_refs), 1)
        self.assertEqual(len(snapshot.opposing_source_refs), 1)
        self.assertNotEqual(
            snapshot.supporting_source_refs,
            snapshot.opposing_source_refs,
        )

    def test_source_resolves_back_to_application_locator(self):
        ref = SOURCE_REFS_BY_LOCATOR["knowledge.DocumentSection:220:12"]

        source = REALISTIC_CONTEXT.resolve_source(ref)

        self.assertEqual(source.kind, SourceKind.DOCUMENT_SECTION)
        self.assertEqual(
            source.internal_locator,
            "knowledge.DocumentSection:220:12",
        )

    def test_empty_catalog_is_allowed_at_context_boundary(self):
        context = build_investigation_context(sources=[])

        self.assertEqual(context.sources, ())
        self.assertEqual(context.consulted_source_refs, ())
        self.assertEqual(len(context.source_catalog), 0)

    def test_duplicate_source_locator_is_rejected(self):
        with self.assertRaises(ContextBuildError):
            build_investigation_context(
                sources=[SOURCE_CANDIDATES[0], SOURCE_CANDIDATES[0]]
            )

    def test_duplicate_registered_hypothesis_locator_is_rejected(self):
        hypothesis = REGISTERED_HYPOTHESIS_CANDIDATES[0]
        with self.assertRaises(ContextBuildError):
            build_investigation_context(
                sources=SOURCE_CANDIDATES,
                registered_hypotheses=[hypothesis, hypothesis],
            )

    def test_registered_hypothesis_relation_requires_known_evidence(self):
        invalid = RegisteredHypothesisCandidate(
            internal_locator="knowledge.Hypothesis:999",
            statement="Hipótese fictícia.",
            observed_status="ACTIVE",
            supporting_source_locators=("knowledge.Evidence:missing",),
        )
        with self.assertRaises(ContextBuildError):
            build_investigation_context(
                sources=SOURCE_CANDIDATES,
                registered_hypotheses=[invalid],
            )

    def test_registered_hypothesis_relation_rejects_non_evidence_source(self):
        invalid = RegisteredHypothesisCandidate(
            internal_locator="knowledge.Hypothesis:999",
            statement="Hipótese fictícia.",
            observed_status="ACTIVE",
            supporting_source_locators=("maintenance.Incident:4102",),
        )
        with self.assertRaises(ContextBuildError):
            build_investigation_context(
                sources=SOURCE_CANDIDATES,
                registered_hypotheses=[invalid],
            )


class ConsultedAndCitedSourceTests(TestCase):
    def test_consulted_and_cited_sources_are_derived_separately(self):
        result = _parse(simulated_payload())

        consulted = REALISTIC_CONTEXT.consulted_source_refs
        cited = REALISTIC_CONTEXT.cited_source_refs(result)

        self.assertGreater(len(consulted), len(cited))
        self.assertTrue(set(cited).issubset(set(consulted)))
        self.assertIn(
            SOURCE_REFS_BY_LOCATOR["assets.Equipment:730"],
            consulted,
        )
        self.assertNotIn(
            SOURCE_REFS_BY_LOCATOR["assets.Equipment:730"],
            cited,
        )

    def test_multiple_citations_of_same_source_are_deduplicated_in_first_use_order(self):
        payload = simulated_payload()
        first_ref = payload["problem"]["source_refs"][0]
        payload["known_facts"][0]["source_refs"] = [first_ref]
        result = _parse(payload)

        cited = REALISTIC_CONTEXT.cited_source_refs(result)

        self.assertEqual(cited.count(first_ref), 1)
        self.assertEqual(cited[0], first_ref)

    def test_display_label_is_not_authority_for_citation(self):
        payload = simulated_payload()
        payload["problem"]["source_refs"] = ["Ocorrência atual"]

        with self.assertRaises(ReferenceContractError):
            _parse(payload)


class ProducerRepresentationSecurityTests(TestCase):
    def test_producer_representation_contains_only_authorized_fields(self):
        representation = REALISTIC_CONTEXT.to_producer_dict()

        for source in representation["sources"]:
            self.assertEqual(set(source), {"ref", "kind", "content"})
        for hypothesis in representation["registered_hypotheses"]:
            self.assertEqual(
                set(hypothesis),
                {
                    "ref",
                    "statement",
                    "observed_status",
                    "supporting_source_refs",
                    "opposing_source_refs",
                },
            )

    def test_internal_locators_and_display_labels_do_not_reach_producer(self):
        encoded = REALISTIC_CONTEXT.to_producer_json()

        self.assertNotIn("internal_locator", encoded)
        self.assertNotIn("display_label", encoded)
        for source in REALISTIC_CONTEXT.sources:
            self.assertNotIn(source.internal_locator, encoded)
        for hypothesis in REALISTIC_CONTEXT.registered_hypotheses:
            self.assertNotIn(hypothesis.internal_locator, encoded)
        for internal_pk in (
            "4102",
            "730",
            "880",
            "901",
            "903",
            "904",
            "3990",
            "602",
            "220",
            "950",
        ):
            self.assertNotIn(internal_pk, encoded)

    def test_untrusted_source_content_remains_data(self):
        unsafe = SourceCandidate(
            internal_locator="knowledge.DocumentSection:unsafe",
            kind=SourceKind.DOCUMENT_SECTION,
            authorized_content="<script>alert(1)</script> **Markdown**",
            display_label="Fonte não confiável",
        )
        context = build_investigation_context(sources=[unsafe])

        representation = context.to_producer_dict()

        self.assertEqual(
            representation["sources"][0]["content"],
            "<script>alert(1)</script> **Markdown**",
        )


class InvestigationContextDeterminismTests(TestCase):
    def mapping_for(self, context):
        return {
            source.internal_locator: source.ref for source in context.sources
        }

    def test_same_context_produces_same_mapping(self):
        first = build_investigation_context(
            sources=SOURCE_CANDIDATES,
            registered_hypotheses=REGISTERED_HYPOTHESIS_CANDIDATES,
        )
        second = build_investigation_context(
            sources=SOURCE_CANDIDATES,
            registered_hypotheses=REGISTERED_HYPOTHESIS_CANDIDATES,
        )

        self.assertEqual(self.mapping_for(first), self.mapping_for(second))
        self.assertEqual(first.to_producer_dict(), second.to_producer_dict())

    def test_reordering_inputs_does_not_change_refs_or_create_collisions(self):
        original = build_investigation_context(
            sources=SOURCE_CANDIDATES,
            registered_hypotheses=REGISTERED_HYPOTHESIS_CANDIDATES,
        )
        reordered = build_investigation_context(
            sources=reversed(SOURCE_CANDIDATES),
            registered_hypotheses=reversed(REGISTERED_HYPOTHESIS_CANDIDATES),
        )

        self.assertEqual(self.mapping_for(original), self.mapping_for(reordered))
        self.assertEqual(
            len(reordered.consulted_source_refs),
            len(set(reordered.consulted_source_refs)),
        )
        self.assertEqual(
            [item.ref for item in original.registered_hypotheses],
            [item.ref for item in reordered.registered_hypotheses],
        )

    def test_registered_hypothesis_mapping_is_order_independent(self):
        second = RegisteredHypothesisCandidate(
            internal_locator="knowledge.Hypothesis:951",
            statement="Segunda hipótese registrada.",
            observed_status="WEAKENED",
        )
        candidates = (*REGISTERED_HYPOTHESIS_CANDIDATES, second)
        first_context = build_investigation_context(
            sources=SOURCE_CANDIDATES,
            registered_hypotheses=candidates,
        )
        second_context = build_investigation_context(
            sources=SOURCE_CANDIDATES,
            registered_hypotheses=reversed(candidates),
        )

        first_mapping = {
            item.internal_locator: item.ref
            for item in first_context.registered_hypotheses
        }
        second_mapping = {
            item.internal_locator: item.ref
            for item in second_context.registered_hypotheses
        }
        self.assertEqual(first_mapping, second_mapping)

    def test_source_identity_comes_from_locator_not_content(self):
        original = SOURCE_CANDIDATES[0]
        changed_content = SourceCandidate(
            internal_locator=original.internal_locator,
            kind=original.kind,
            authorized_content="Conteúdo alterado para o mesmo objeto autorizado.",
            display_label=original.display_label,
        )
        same_text_other_identity = SourceCandidate(
            internal_locator="maintenance.Incident:other",
            kind=original.kind,
            authorized_content=original.authorized_content,
            display_label="Outra ocorrência",
        )

        original_context = build_investigation_context(sources=[original])
        changed_context = build_investigation_context(sources=[changed_content])
        distinct_context = build_investigation_context(
            sources=[original, same_text_other_identity]
        )

        self.assertEqual(
            original_context.sources[0].ref,
            changed_context.sources[0].ref,
        )
        self.assertEqual(len({item.ref for item in distinct_context.sources}), 2)


class MachineReadableSchemaTests(TestCase):
    def test_schema_is_json_serializable_and_returns_an_independent_copy(self):
        first = investigation_result_v1_schema()
        second = investigation_result_v1_schema()

        json.dumps(first)
        first["title"] = "changed"
        self.assertNotEqual(first["title"], second["title"])

    def test_top_level_schema_matches_python_contract(self):
        schema = investigation_result_v1_schema()

        self.assertEqual(set(schema["properties"]), set(TOP_LEVEL_FIELDS))
        self.assertEqual(set(schema["required"]), set(TOP_LEVEL_FIELDS))
        self.assertFalse(schema["additionalProperties"])
        for collection in (
            "known_facts",
            "evidence",
            "contradictions",
            "hypotheses",
            "checks",
        ):
            self.assertEqual(schema["properties"][collection]["type"], "array")
            self.assertNotIn("maxItems", schema["properties"][collection])

    def test_schema_enums_match_python_enums(self):
        hypothesis = investigation_result_v1_schema()["$defs"]["hypothesis"]
        properties = hypothesis["properties"]

        self.assertEqual(
            properties["origin"]["enum"],
            [item.value for item in HypothesisOrigin],
        )
        self.assertEqual(
            properties["evidence_assessment"]["enum"],
            [item.value for item in EvidenceAssessment],
        )

    def test_schema_represents_registered_and_proposed_variants(self):
        hypothesis = investigation_result_v1_schema()["$defs"]["hypothesis"]
        conditional = hypothesis["allOf"][0]

        self.assertEqual(
            conditional["if"]["properties"]["origin"]["const"],
            "REGISTERED",
        )
        self.assertEqual(conditional["then"]["required"], ["registered_ref"])
        self.assertEqual(
            conditional["else"]["not"]["required"],
            ["registered_ref"],
        )

    def test_schema_represents_checks_and_outcomes_without_priority(self):
        definitions = investigation_result_v1_schema()["$defs"]
        check = definitions["check"]

        self.assertEqual(
            set(check["required"]),
            {"action", "reason", "basis_refs", "possible_outcomes"},
        )
        self.assertNotIn("priority", check["properties"])
        self.assertEqual(
            check["properties"]["possible_outcomes"]["items"]["$ref"],
            "#/$defs/check_outcome",
        )

    def test_schema_documents_runtime_only_invariants(self):
        invariants = investigation_result_v1_schema()[
            "x-faulttrace-runtime-invariants"
        ]

        self.assertTrue(any("Source refs exist" in item for item in invariants))
        self.assertTrue(any("disjoint" in item for item in invariants))


class ProducerFailurePolicyTests(TestCase):
    def capture_error(self, payload):
        try:
            _parse(payload)
        except Exception as error:
            return error
        self.fail("Expected payload validation to fail")

    def test_contract_failures_have_distinct_categories(self):
        with self.assertRaises(ContractParseError) as syntax:
            parse_investigation_result_json(
                "{",
                source_catalog=REALISTIC_CONTEXT.source_catalog,
                registered_hypothesis_catalog=(
                    REALISTIC_CONTEXT.registered_hypothesis_catalog
                ),
            )

        structural_payload = simulated_payload()
        structural_payload.pop("checks")
        reference_payload = simulated_payload()
        reference_payload["problem"]["source_refs"] = ["src_missing"]
        semantic_payload = simulated_payload()
        semantic_payload["hypotheses"][0]["opposing_finding_refs"] = []

        cases = (
            (syntax.exception, ProducerFailureCategory.SYNTAX_FAILURE),
            (
                self.capture_error(structural_payload),
                ProducerFailureCategory.STRUCTURAL_CONTRACT_FAILURE,
            ),
            (
                self.capture_error(reference_payload),
                ProducerFailureCategory.REFERENCE_FAILURE,
            ),
            (
                self.capture_error(semantic_payload),
                ProducerFailureCategory.SEMANTIC_INVARIANT_FAILURE,
            ),
        )
        for error, expected in cases:
            with self.subTest(error=error):
                self.assertEqual(classify_contract_exception(error), expected)

    def test_safe_feedback_contains_no_payload_or_internal_data(self):
        payload = simulated_payload()
        payload["problem"]["source_refs"] = ["src_missing"]
        error = self.capture_error(payload)

        feedback = safe_validation_feedback(error)
        encoded = json.dumps(feedback)

        self.assertEqual(feedback["category"], "REFERENCE_FAILURE")
        self.assertIn("path", feedback)
        self.assertNotIn("maintenance.Incident", encoded)
        self.assertNotIn("Traceback", encoded)
        self.assertNotIn("src_missing", encoded)

    def test_retry_policy_is_bounded_and_transport_aware(self):
        transport = ProducerFailureCategory.TRANSPORT_PROVIDER_FAILURE

        self.assertEqual(
            recommended_retry(transport, failures_so_far=1, timed_out=True),
            RetryDisposition.RETRYABLE,
        )
        self.assertEqual(
            recommended_retry(transport, failures_so_far=1, transport_status=429),
            RetryDisposition.RETRYABLE,
        )
        self.assertEqual(
            recommended_retry(transport, failures_so_far=1, transport_status=500),
            RetryDisposition.RETRYABLE,
        )
        self.assertEqual(
            recommended_retry(transport, failures_so_far=1, transport_status=401),
            RetryDisposition.NON_RETRYABLE,
        )
        self.assertEqual(
            recommended_retry(
                ProducerFailureCategory.REFERENCE_FAILURE,
                failures_so_far=1,
            ),
            RetryDisposition.CONDITIONAL,
        )
        self.assertEqual(
            recommended_retry(
                ProducerFailureCategory.SYNTAX_FAILURE,
                failures_so_far=2,
            ),
            RetryDisposition.NON_RETRYABLE,
        )


class PhaseTwentyFiveMeasurementTests(TestCase):
    def test_fixture_measurements_are_collected_without_becoming_limits(self):
        context_payload = REALISTIC_CONTEXT.to_producer_json().encode("utf-8")
        result_sizes = {
            name: len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            for name, payload in CORPUS_A_TO_L.items()
        }

        self.assertEqual(len(REALISTIC_CONTEXT.sources), 10)
        self.assertGreater(len(context_payload), 0)
        self.assertEqual(len(result_sizes), 12)
        self.assertGreater(result_sizes["H_many_evidence"], result_sizes["G_few_evidence"])
        self.assertNotIn("maxLength", json.dumps(investigation_result_v1_schema()))


class ContextDtoIndependenceTests(TestCase):
    def test_context_dtos_do_not_store_orm_objects(self):
        dto_types = (
            SourceCandidate,
            RegisteredHypothesisCandidate,
        )
        field_types = {
            str(field.type)
            for dto_type in dto_types
            for field in fields(dto_type)
        }

        self.assertTrue(all("models.Model" not in value for value in field_types))
