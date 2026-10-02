"""Machine-readable structural schema for InvestigationResultV1.

JSON Schema describes the provider-facing shape.  Cross-object identity,
authorization, and semantic invariants remain authoritative in the Python
validator from ``investigation_contracts``.
"""

from __future__ import annotations

from copy import deepcopy

from .investigation_contracts import (
    EvidenceAssessment,
    HypothesisOrigin,
    TOP_LEVEL_FIELDS,
)


SCHEMA_URI = "https://faulttrace.local/schemas/investigation-result-v1.json"


def investigation_result_v1_schema() -> dict:
    """Return an independent JSON Schema document for contract version 1.0."""

    return deepcopy(_SCHEMA)


def _object(properties, required):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _text():
    return {"type": "string", "minLength": 1, "pattern": r".*\S.*"}


def _ref_array(*, minimum=0):
    schema = {
        "type": "array",
        "items": _text(),
        "uniqueItems": True,
    }
    if minimum:
        schema["minItems"] = minimum
    return schema


_DEFINITIONS = {
    "problem": _object(
        {
            "statement": _text(),
            "source_refs": _ref_array(minimum=1),
        },
        ("statement", "source_refs"),
    ),
    "known_fact": _object(
        {
            "id": {"type": "string", "pattern": r"^fact_[A-Za-z0-9][A-Za-z0-9_-]*$"},
            "statement": _text(),
            "source_refs": _ref_array(minimum=1),
        },
        ("id", "statement", "source_refs"),
    ),
    "evidence": _object(
        {
            "id": {
                "type": "string",
                "pattern": r"^evidence_[A-Za-z0-9][A-Za-z0-9_-]*$",
            },
            "statement": _text(),
            "significance": _text(),
            "source_refs": _ref_array(minimum=1),
        },
        ("id", "statement", "significance", "source_refs"),
    ),
    "contradiction": _object(
        {
            "id": {
                "type": "string",
                "pattern": r"^contradiction_[A-Za-z0-9][A-Za-z0-9_-]*$",
            },
            "finding_refs": {
                **_ref_array(),
                "minItems": 2,
                "maxItems": 2,
            },
            "explanation": _text(),
            "implication": _text(),
        },
        ("id", "finding_refs", "explanation", "implication"),
    ),
    "hypothesis": {
        **_object(
            {
                "id": {
                    "type": "string",
                    "pattern": r"^hypothesis_[A-Za-z0-9][A-Za-z0-9_-]*$",
                },
                "origin": {
                    "type": "string",
                    "enum": [item.value for item in HypothesisOrigin],
                },
                "registered_ref": _text(),
                "statement": _text(),
                "evidence_assessment": {
                    "type": "string",
                    "enum": [item.value for item in EvidenceAssessment],
                },
                "supporting_finding_refs": _ref_array(),
                "opposing_finding_refs": _ref_array(),
                "rationale": _text(),
            },
            (
                "id",
                "origin",
                "statement",
                "evidence_assessment",
                "supporting_finding_refs",
                "opposing_finding_refs",
                "rationale",
            ),
        ),
        "allOf": [
            {
                "if": {
                    "properties": {"origin": {"const": "REGISTERED"}},
                    "required": ["origin"],
                },
                "then": {"required": ["registered_ref"]},
                "else": {"not": {"required": ["registered_ref"]}},
            }
        ],
    },
    "check_outcome": _object(
        {"observation": _text(), "implication": _text()},
        ("observation", "implication"),
    ),
    "check": _object(
        {
            "action": _text(),
            "reason": _text(),
            "basis_refs": _ref_array(minimum=1),
            "possible_outcomes": {
                "type": "array",
                "items": {"$ref": "#/$defs/check_outcome"},
            },
        },
        ("action", "reason", "basis_refs", "possible_outcomes"),
    ),
}


_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SCHEMA_URI,
    "title": "FaultTrace InvestigationResultV1",
    "type": "object",
    "properties": {
        "schema_version": {"const": "1.0"},
        "problem": {"$ref": "#/$defs/problem"},
        "summary": _text(),
        "known_facts": {
            "type": "array",
            "items": {"$ref": "#/$defs/known_fact"},
        },
        "evidence": {
            "type": "array",
            "items": {"$ref": "#/$defs/evidence"},
        },
        "contradictions": {
            "type": "array",
            "items": {"$ref": "#/$defs/contradiction"},
        },
        "hypotheses": {
            "type": "array",
            "items": {"$ref": "#/$defs/hypothesis"},
        },
        "checks": {
            "type": "array",
            "items": {"$ref": "#/$defs/check"},
        },
    },
    "required": sorted(TOP_LEVEL_FIELDS),
    "additionalProperties": False,
    "$defs": _DEFINITIONS,
    "x-faulttrace-runtime-invariants": [
        "Local IDs are unique across the result.",
        "Source refs exist in the authorized SourceCatalog.",
        "Registered refs exist in the authorized hypothesis catalog.",
        "Local refs exist and point to an allowed analysis type.",
        "Supporting and opposing refs are disjoint.",
        "Assessment-specific minimum relations are satisfied.",
        "A registered hypothesis ref is used at most once.",
    ],
}
