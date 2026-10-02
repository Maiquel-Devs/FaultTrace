import json
from copy import deepcopy
from dataclasses import fields
from unittest import TestCase

from .investigation_contracts import (
    Check,
    CheckOutcome,
    ContractParseError,
    ContractValidationError,
    Contradiction,
    EvidenceItem,
    HypothesisAnalysis,
    InvestigationResultV1,
    KnownFact,
    ProblemStatement,
    RegisteredHypothesisCatalog,
    SourceCatalog,
    SourceCatalogEntry,
    parse_investigation_result,
    parse_investigation_result_json,
)
from .testdata.investigation_contract_fixtures import (
    CORPUS_A_TO_L,
    REGISTERED_HYPOTHESIS_CATALOG,
    SOURCE_CATALOG,
    valid_payload,
)


def _parse(payload):
    return parse_investigation_result(
        payload,
        source_catalog=SOURCE_CATALOG,
        registered_hypothesis_catalog=REGISTERED_HYPOTHESIS_CATALOG,
    )


class InvestigationContractCorpusTests(TestCase):
    def test_every_a_to_l_fixture_uses_the_same_contract(self):
        for name, payload in CORPUS_A_TO_L.items():
            with self.subTest(name=name):
                result = _parse(payload)
                self.assertIsInstance(result, InvestigationResultV1)
                self.assertEqual(result.schema_version, "1.0")

    def test_explicit_empty_states_remain_empty_arrays(self):
        expected = {
            "J_no_contradiction": "contradictions",
            "K_no_hypothesis": "hypotheses",
            "L_no_checks": "checks",
        }
        for fixture_name, field_name in expected.items():
            with self.subTest(fixture=fixture_name, field=field_name):
                result = _parse(CORPUS_A_TO_L[fixture_name])
                self.assertEqual(getattr(result, field_name), ())
                self.assertEqual(result.to_dict()[field_name], [])

    def test_insufficient_hypotheses_are_allowed_with_rationale(self):
        result = _parse(CORPUS_A_TO_L["E_pressure"])

        self.assertEqual(len(result.hypotheses), 2)
        for hypothesis in result.hypotheses:
            self.assertEqual(hypothesis.evidence_assessment.value, "INSUFFICIENT")
            self.assertTrue(hypothesis.rationale)

    def test_evidence_can_have_different_effects_on_different_hypotheses(self):
        payload = valid_payload()
        payload["hypotheses"].append(
            {
                "id": "hypothesis_2",
                "origin": "PROPOSED",
                "statement": "A condição pode ter uma origem alternativa.",
                "evidence_assessment": "LEANS_OPPOSING",
                "supporting_finding_refs": [],
                "opposing_finding_refs": ["evidence_1"],
                "rationale": "A mesma evidência pesa contra esta explicação.",
            }
        )

        result = _parse(payload)

        self.assertIn(
            "evidence_1", result.hypotheses[0].supporting_finding_refs
        )
        self.assertIn("evidence_1", result.hypotheses[1].opposing_finding_refs)


class InvestigationContractAdversarialTests(TestCase):
    def assert_invalid(self, mutate):
        payload = valid_payload()
        mutate(payload)
        with self.assertRaises(ContractValidationError):
            _parse(payload)

    def test_unknown_source_ref_is_rejected(self):
        self.assert_invalid(
            lambda payload: payload["evidence"][0].update(
                source_refs=["src_invented"]
            )
        )

    def test_unknown_registered_ref_is_rejected(self):
        self.assert_invalid(
            lambda payload: payload["hypotheses"][0].update(
                registered_ref="ctx_hypothesis_missing"
            )
        )

    def test_duplicate_local_id_is_rejected(self):
        def mutate(payload):
            payload["known_facts"].append(deepcopy(payload["known_facts"][0]))

        self.assert_invalid(mutate)

    def test_wrong_id_prefix_is_rejected(self):
        self.assert_invalid(
            lambda payload: payload["evidence"][0].update(id="fact_wrong")
        )

    def test_unknown_finding_ref_is_rejected(self):
        def mutate(payload):
            payload["contradictions"] = [
                {
                    "id": "contradiction_1",
                    "finding_refs": ["fact_1", "evidence_missing"],
                    "explanation": "Conflito de teste.",
                    "implication": "Exige verificação.",
                }
            ]

        self.assert_invalid(mutate)

    def test_contradiction_requires_exactly_two_findings(self):
        for refs in (["fact_1"], ["fact_1", "evidence_1", "fact_2"]):
            with self.subTest(refs=refs):
                def mutate(payload, selected_refs=refs):
                    payload["contradictions"] = [
                        {
                            "id": "contradiction_1",
                            "finding_refs": selected_refs,
                            "explanation": "Conflito de teste.",
                            "implication": "Exige verificação.",
                        }
                    ]

                self.assert_invalid(mutate)

    def test_contradiction_cannot_reference_hypothesis(self):
        def mutate(payload):
            payload["contradictions"] = [
                {
                    "id": "contradiction_1",
                    "finding_refs": ["fact_1", "hypothesis_1"],
                    "explanation": "Conflito de teste.",
                    "implication": "Exige verificação.",
                }
            ]

        self.assert_invalid(mutate)

    def test_hypothesis_without_statement_is_rejected(self):
        self.assert_invalid(
            lambda payload: payload["hypotheses"][0].pop("statement")
        )

    def test_check_without_action_is_rejected(self):
        self.assert_invalid(lambda payload: payload["checks"][0].pop("action"))

    def test_unknown_enum_is_rejected(self):
        self.assert_invalid(
            lambda payload: payload["hypotheses"][0].update(
                evidence_assessment="CONFIRMED"
            )
        )

    def test_null_collection_is_rejected(self):
        for field_name in (
            "known_facts",
            "evidence",
            "contradictions",
            "hypotheses",
            "checks",
        ):
            with self.subTest(field=field_name):
                self.assert_invalid(
                    lambda payload, field=field_name: payload.update({field: None})
                )

    def test_missing_top_level_field_is_rejected(self):
        self.assert_invalid(lambda payload: payload.pop("contradictions"))

    def test_unexpected_field_is_rejected_at_every_level(self):
        mutations = (
            lambda payload: payload.update(layout="table"),
            lambda payload: payload["problem"].update(html="<p>problem</p>"),
            lambda payload: payload["evidence"][0].update(relation="SUPPORTS"),
            lambda payload: payload["checks"][0].update(priority="HIGH"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.assert_invalid(mutate)

    def test_registered_hypothesis_requires_registered_ref(self):
        self.assert_invalid(
            lambda payload: payload["hypotheses"][0].pop("registered_ref")
        )

    def test_proposed_hypothesis_forbids_registered_ref(self):
        def mutate(payload):
            payload["hypotheses"][0]["origin"] = "PROPOSED"

        self.assert_invalid(mutate)

    def test_assessment_minimum_relations_are_enforced(self):
        cases = (
            ("MIXED", [], ["evidence_1"]),
            ("MIXED", ["evidence_1"], []),
            ("LEANS_SUPPORTING", [], []),
            ("LEANS_OPPOSING", [], []),
        )
        for assessment, supporting, opposing in cases:
            with self.subTest(assessment=assessment, supporting=supporting):
                def mutate(payload):
                    hypothesis = payload["hypotheses"][0]
                    hypothesis["evidence_assessment"] = assessment
                    hypothesis["supporting_finding_refs"] = supporting
                    hypothesis["opposing_finding_refs"] = opposing

                self.assert_invalid(mutate)

    def test_same_finding_cannot_support_and_oppose(self):
        def mutate(payload):
            payload["hypotheses"][0]["opposing_finding_refs"] = ["evidence_1"]

        self.assert_invalid(mutate)

    def test_hypothesis_cannot_reference_another_hypothesis(self):
        self.assert_invalid(
            lambda payload: payload["hypotheses"][0].update(
                supporting_finding_refs=["hypothesis_1"]
            )
        )

    def test_duplicate_reference_is_rejected_not_normalized(self):
        self.assert_invalid(
            lambda payload: payload["problem"].update(
                source_refs=["src_1", "src_1"]
            )
        )

    def test_unknown_check_basis_is_rejected(self):
        self.assert_invalid(
            lambda payload: payload["checks"][0].update(
                basis_refs=["evidence_missing"]
            )
        )

    def test_check_cannot_reference_another_check(self):
        self.assert_invalid(
            lambda payload: payload["checks"][0].update(basis_refs=["check_1"])
        )

    def test_empty_and_whitespace_only_required_strings_are_rejected(self):
        mutations = (
            lambda payload, value: payload["problem"].update(statement=value),
            lambda payload, value: payload.update(summary=value),
            lambda payload, value: payload["known_facts"][0].update(statement=value),
            lambda payload, value: payload["evidence"][0].update(significance=value),
            lambda payload, value: payload["hypotheses"][0].update(rationale=value),
            lambda payload, value: payload["checks"][0].update(reason=value),
        )
        for value in ("", "  \t\n  "):
            for mutate in mutations:
                with self.subTest(value=repr(value), mutate=mutate):
                    self.assert_invalid(lambda payload: mutate(payload, value))

    def test_same_registered_hypothesis_cannot_be_used_twice(self):
        def mutate(payload):
            duplicate = deepcopy(payload["hypotheses"][0])
            duplicate["id"] = "hypothesis_2"
            payload["hypotheses"].append(duplicate)

        self.assert_invalid(mutate)

    def test_truncated_json_fails_before_semantic_validation(self):
        with self.assertRaises(ContractParseError):
            parse_investigation_result_json(
                '{"schema_version":"1.0",',
                source_catalog=SOURCE_CATALOG,
                registered_hypothesis_catalog=REGISTERED_HYPOTHESIS_CATALOG,
            )


class InvestigationContractSerializationTests(TestCase):
    def test_python_json_parse_round_trip_preserves_contract(self):
        original = _parse(CORPUS_A_TO_L["H_many_evidence"])

        encoded = original.to_json()
        reparsed = parse_investigation_result_json(
            encoded,
            source_catalog=SOURCE_CATALOG,
            registered_hypothesis_catalog=REGISTERED_HYPOTHESIS_CATALOG,
        )

        self.assertEqual(reparsed, original)
        self.assertEqual(reparsed.to_dict(), original.to_dict())
        self.assertEqual(
            [check.action for check in reparsed.checks],
            [check.action for check in original.checks],
        )
        self.assertIsInstance(json.loads(encoded)["checks"], list)
        self.assertEqual(json.loads(encoded)["contradictions"], original.to_dict()["contradictions"])

    def test_safe_normalization_trims_and_normalizes_unicode(self):
        payload = valid_payload()
        payload["summary"] = "  medição analisada  "

        result = _parse(payload)

        self.assertEqual(result.summary, "medição analisada")


class InvestigationContractSecurityTests(TestCase):
    def test_untrusted_markup_remains_plain_data(self):
        malicious_values = (
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "javascript:alert(1)",
            "[link](javascript:alert(1)) **Markdown**",
            "<section data-x='arbitrary'>HTML</section>",
        )
        for value in malicious_values:
            with self.subTest(value=value):
                payload = valid_payload()
                payload["summary"] = value

                result = _parse(payload)
                encoded = result.to_json()

                self.assertEqual(result.summary, value)
                self.assertEqual(json.loads(encoded)["summary"], value)
                self.assertNotIn("mark_safe", encoded)


class InvestigationContractIndependenceTests(TestCase):
    contract_types = (
        ProblemStatement,
        KnownFact,
        EvidenceItem,
        Contradiction,
        HypothesisAnalysis,
        CheckOutcome,
        Check,
        InvestigationResultV1,
        SourceCatalogEntry,
    )

    def schema_field_names(self):
        return {
            field.name.lower()
            for contract_type in self.contract_types
            for field in fields(contract_type)
        }

    def test_schema_has_no_ui_concepts(self):
        forbidden = {"table", "card", "column", "css", "html", "markdown", "bootstrap"}

        self.assertTrue(self.schema_field_names().isdisjoint(forbidden))

    def test_schema_has_no_equipment_specific_concepts(self):
        forbidden = {
            "current",
            "voltage",
            "temperature",
            "pressure",
            "vibration",
            "sensor",
            "plc",
            "motor",
            "compressor",
        }

        self.assertTrue(self.schema_field_names().isdisjoint(forbidden))


class InvestigationCatalogTests(TestCase):
    def test_source_catalog_rejects_duplicate_refs(self):
        entry = SourceCatalogEntry("src_1", "TEST", "Fonte")
        with self.assertRaises(ContractValidationError):
            SourceCatalog([entry, entry])

    def test_registered_hypothesis_catalog_rejects_duplicate_refs(self):
        with self.assertRaises(ContractValidationError):
            RegisteredHypothesisCatalog(["ctx_hypothesis_1", "ctx_hypothesis_1"])
