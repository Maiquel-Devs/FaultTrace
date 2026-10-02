"""Provider-independent semantic contract for structured investigations.

This module is intentionally isolated from the current Agent, persistence, and
presentation pipelines.  It validates untrusted mappings against the V1
contract and resolves references only against catalogs supplied by the caller.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence


class ContractValidationError(ValueError):
    """Raised when a payload violates the InvestigationResultV1 contract."""

    def __init__(self, path: str, message: str):
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


class StructuralContractError(ContractValidationError):
    """Raised when JSON values do not match the declared V1 structure."""


class ReferenceContractError(ContractValidationError):
    """Raised when identity or reference integrity is violated."""


class SemanticInvariantError(ContractValidationError):
    """Raised when a cross-field semantic invariant is violated."""


class ContractParseError(ValueError):
    """Raised when text is not valid JSON."""


class HypothesisOrigin(str, Enum):
    REGISTERED = "REGISTERED"
    PROPOSED = "PROPOSED"


class EvidenceAssessment(str, Enum):
    INSUFFICIENT = "INSUFFICIENT"
    MIXED = "MIXED"
    LEANS_SUPPORTING = "LEANS_SUPPORTING"
    LEANS_OPPOSING = "LEANS_OPPOSING"


@dataclass(frozen=True)
class SourceCatalogEntry:
    ref: str
    kind: str
    display_label: str

    def __post_init__(self):
        object.__setattr__(self, "ref", _text(self.ref, "source.ref"))
        object.__setattr__(self, "kind", _text(self.kind, "source.kind"))
        object.__setattr__(
            self,
            "display_label",
            _text(self.display_label, "source.display_label"),
        )


class SourceCatalog:
    """Authorized sources available to one investigation request."""

    def __init__(self, entries: Iterable[SourceCatalogEntry]):
        by_ref: dict[str, SourceCatalogEntry] = {}
        for entry in entries:
            if not isinstance(entry, SourceCatalogEntry):
                raise TypeError("SourceCatalog accepts SourceCatalogEntry values.")
            if entry.ref in by_ref:
                raise ReferenceContractError(
                    "source_catalog", f"duplicate source reference {entry.ref!r}"
                )
            by_ref[entry.ref] = entry
        self._by_ref = by_ref

    def __contains__(self, ref: str) -> bool:
        return ref in self._by_ref

    def __len__(self) -> int:
        return len(self._by_ref)

    @property
    def refs(self) -> frozenset[str]:
        return frozenset(self._by_ref)

    def resolve(self, ref: str) -> SourceCatalogEntry:
        return self._by_ref[ref]


class RegisteredHypothesisCatalog:
    """Opaque references to hypotheses registered in the authorized context."""

    def __init__(self, refs: Iterable[str] = ()):
        normalized: set[str] = set()
        for index, value in enumerate(refs):
            ref = _text(value, f"registered_hypothesis_catalog[{index}]")
            if ref in normalized:
                raise ReferenceContractError(
                    "registered_hypothesis_catalog",
                    f"duplicate registered hypothesis reference {ref!r}",
                )
            normalized.add(ref)
        self._refs = frozenset(normalized)

    def __contains__(self, ref: str) -> bool:
        return ref in self._refs

    def __len__(self) -> int:
        return len(self._refs)

    @property
    def refs(self) -> frozenset[str]:
        return self._refs


@dataclass(frozen=True)
class ProblemStatement:
    statement: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class KnownFact:
    id: str
    statement: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    statement: str
    significance: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class Contradiction:
    id: str
    finding_refs: tuple[str, str]
    explanation: str
    implication: str


@dataclass(frozen=True)
class HypothesisAnalysis:
    id: str
    origin: HypothesisOrigin
    statement: str
    evidence_assessment: EvidenceAssessment
    supporting_finding_refs: tuple[str, ...]
    opposing_finding_refs: tuple[str, ...]
    rationale: str
    registered_ref: str | None = None


@dataclass(frozen=True)
class CheckOutcome:
    observation: str
    implication: str


@dataclass(frozen=True)
class Check:
    action: str
    reason: str
    basis_refs: tuple[str, ...]
    possible_outcomes: tuple[CheckOutcome, ...]


@dataclass(frozen=True)
class InvestigationResultV1:
    schema_version: str
    problem: ProblemStatement
    summary: str
    known_facts: tuple[KnownFact, ...]
    evidence: tuple[EvidenceItem, ...]
    contradictions: tuple[Contradiction, ...]
    hypotheses: tuple[HypothesisAnalysis, ...]
    checks: tuple[Check, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "problem": {
                "statement": self.problem.statement,
                "source_refs": list(self.problem.source_refs),
            },
            "summary": self.summary,
            "known_facts": [
                {
                    "id": item.id,
                    "statement": item.statement,
                    "source_refs": list(item.source_refs),
                }
                for item in self.known_facts
            ],
            "evidence": [
                {
                    "id": item.id,
                    "statement": item.statement,
                    "significance": item.significance,
                    "source_refs": list(item.source_refs),
                }
                for item in self.evidence
            ],
            "contradictions": [
                {
                    "id": item.id,
                    "finding_refs": list(item.finding_refs),
                    "explanation": item.explanation,
                    "implication": item.implication,
                }
                for item in self.contradictions
            ],
            "hypotheses": [
                {
                    "id": item.id,
                    "origin": item.origin.value,
                    "statement": item.statement,
                    "evidence_assessment": item.evidence_assessment.value,
                    "supporting_finding_refs": list(
                        item.supporting_finding_refs
                    ),
                    "opposing_finding_refs": list(item.opposing_finding_refs),
                    "rationale": item.rationale,
                    **(
                        {"registered_ref": item.registered_ref}
                        if item.origin is HypothesisOrigin.REGISTERED
                        else {}
                    ),
                }
                for item in self.hypotheses
            ],
            "checks": [
                {
                    "action": item.action,
                    "reason": item.reason,
                    "basis_refs": list(item.basis_refs),
                    "possible_outcomes": [
                        {
                            "observation": outcome.observation,
                            "implication": outcome.implication,
                        }
                        for outcome in item.possible_outcomes
                    ],
                }
                for item in self.checks
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "problem",
        "summary",
        "known_facts",
        "evidence",
        "contradictions",
        "hypotheses",
        "checks",
    }
)

_ID_PATTERNS = {
    "fact": re.compile(r"^fact_[A-Za-z0-9][A-Za-z0-9_-]*$"),
    "evidence": re.compile(r"^evidence_[A-Za-z0-9][A-Za-z0-9_-]*$"),
    "contradiction": re.compile(
        r"^contradiction_[A-Za-z0-9][A-Za-z0-9_-]*$"
    ),
    "hypothesis": re.compile(r"^hypothesis_[A-Za-z0-9][A-Za-z0-9_-]*$"),
}


def parse_investigation_result(
    payload: Mapping[str, Any],
    *,
    source_catalog: SourceCatalog,
    registered_hypothesis_catalog: RegisteredHypothesisCatalog,
) -> InvestigationResultV1:
    """Parse and validate an untrusted mapping as InvestigationResultV1."""

    root = _object(payload, "$", TOP_LEVEL_FIELDS)
    schema_version = _text(root["schema_version"], "$.schema_version")
    if schema_version != "1.0":
        raise StructuralContractError(
            "$.schema_version", "expected the literal '1.0'"
        )

    problem = _parse_problem(root["problem"])
    summary = _text(root["summary"], "$.summary")
    known_facts = tuple(
        _parse_fact(item, f"$.known_facts[{index}]")
        for index, item in enumerate(_array(root["known_facts"], "$.known_facts"))
    )
    evidence = tuple(
        _parse_evidence(item, f"$.evidence[{index}]")
        for index, item in enumerate(_array(root["evidence"], "$.evidence"))
    )
    contradictions = tuple(
        _parse_contradiction(item, f"$.contradictions[{index}]")
        for index, item in enumerate(
            _array(root["contradictions"], "$.contradictions")
        )
    )
    hypotheses = tuple(
        _parse_hypothesis(item, f"$.hypotheses[{index}]")
        for index, item in enumerate(_array(root["hypotheses"], "$.hypotheses"))
    )
    checks = tuple(
        _parse_check(item, f"$.checks[{index}]")
        for index, item in enumerate(_array(root["checks"], "$.checks"))
    )

    result = InvestigationResultV1(
        schema_version=schema_version,
        problem=problem,
        summary=summary,
        known_facts=known_facts,
        evidence=evidence,
        contradictions=contradictions,
        hypotheses=hypotheses,
        checks=checks,
    )
    _validate_references(
        result,
        source_catalog=source_catalog,
        registered_hypothesis_catalog=registered_hypothesis_catalog,
    )
    return result


def parse_investigation_result_json(
    value: str,
    *,
    source_catalog: SourceCatalog,
    registered_hypothesis_catalog: RegisteredHypothesisCatalog,
) -> InvestigationResultV1:
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise ContractParseError("Investigation result is not valid JSON.") from error
    return parse_investigation_result(
        payload,
        source_catalog=source_catalog,
        registered_hypothesis_catalog=registered_hypothesis_catalog,
    )


def _parse_problem(value: Any) -> ProblemStatement:
    path = "$.problem"
    item = _object(value, path, {"statement", "source_refs"})
    return ProblemStatement(
        statement=_text(item["statement"], f"{path}.statement"),
        source_refs=_refs(item["source_refs"], f"{path}.source_refs", minimum=1),
    )


def _parse_fact(value: Any, path: str) -> KnownFact:
    item = _object(value, path, {"id", "statement", "source_refs"})
    return KnownFact(
        id=_local_id(item["id"], f"{path}.id", "fact"),
        statement=_text(item["statement"], f"{path}.statement"),
        source_refs=_refs(item["source_refs"], f"{path}.source_refs", minimum=1),
    )


def _parse_evidence(value: Any, path: str) -> EvidenceItem:
    item = _object(
        value, path, {"id", "statement", "significance", "source_refs"}
    )
    return EvidenceItem(
        id=_local_id(item["id"], f"{path}.id", "evidence"),
        statement=_text(item["statement"], f"{path}.statement"),
        significance=_text(item["significance"], f"{path}.significance"),
        source_refs=_refs(item["source_refs"], f"{path}.source_refs", minimum=1),
    )


def _parse_contradiction(value: Any, path: str) -> Contradiction:
    item = _object(
        value,
        path,
        {"id", "finding_refs", "explanation", "implication"},
    )
    finding_refs = _refs(item["finding_refs"], f"{path}.finding_refs")
    if len(finding_refs) != 2:
        raise StructuralContractError(
            f"{path}.finding_refs", "expected exactly two distinct finding references"
        )
    return Contradiction(
        id=_local_id(item["id"], f"{path}.id", "contradiction"),
        finding_refs=(finding_refs[0], finding_refs[1]),
        explanation=_text(item["explanation"], f"{path}.explanation"),
        implication=_text(item["implication"], f"{path}.implication"),
    )


def _parse_hypothesis(value: Any, path: str) -> HypothesisAnalysis:
    required = {
        "id",
        "origin",
        "statement",
        "evidence_assessment",
        "supporting_finding_refs",
        "opposing_finding_refs",
        "rationale",
    }
    item = _object(value, path, required, {"registered_ref"})
    origin = _enum(item["origin"], f"{path}.origin", HypothesisOrigin)
    has_registered_ref = "registered_ref" in item
    if origin is HypothesisOrigin.REGISTERED and not has_registered_ref:
        raise StructuralContractError(
            path, "REGISTERED hypotheses require registered_ref"
        )
    if origin is HypothesisOrigin.PROPOSED and has_registered_ref:
        raise StructuralContractError(
            path, "PROPOSED hypotheses must not contain registered_ref"
        )
    registered_ref = (
        _text(item["registered_ref"], f"{path}.registered_ref")
        if has_registered_ref
        else None
    )
    supporting = _refs(
        item["supporting_finding_refs"], f"{path}.supporting_finding_refs"
    )
    opposing = _refs(
        item["opposing_finding_refs"], f"{path}.opposing_finding_refs"
    )
    overlap = set(supporting) & set(opposing)
    if overlap:
        raise SemanticInvariantError(
            path,
            "supporting and opposing references must be disjoint: "
            + ", ".join(sorted(overlap)),
        )
    assessment = _enum(
        item["evidence_assessment"],
        f"{path}.evidence_assessment",
        EvidenceAssessment,
    )
    if assessment is EvidenceAssessment.MIXED and (not supporting or not opposing):
        raise SemanticInvariantError(
            path, "MIXED requires supporting and opposing findings"
        )
    if assessment is EvidenceAssessment.LEANS_SUPPORTING and not supporting:
        raise SemanticInvariantError(
            path, "LEANS_SUPPORTING requires a supporting finding"
        )
    if assessment is EvidenceAssessment.LEANS_OPPOSING and not opposing:
        raise SemanticInvariantError(
            path, "LEANS_OPPOSING requires an opposing finding"
        )
    return HypothesisAnalysis(
        id=_local_id(item["id"], f"{path}.id", "hypothesis"),
        origin=origin,
        statement=_text(item["statement"], f"{path}.statement"),
        evidence_assessment=assessment,
        supporting_finding_refs=supporting,
        opposing_finding_refs=opposing,
        rationale=_text(item["rationale"], f"{path}.rationale"),
        registered_ref=registered_ref,
    )


def _parse_check(value: Any, path: str) -> Check:
    item = _object(
        value, path, {"action", "reason", "basis_refs", "possible_outcomes"}
    )
    outcomes = tuple(
        _parse_outcome(outcome, f"{path}.possible_outcomes[{index}]")
        for index, outcome in enumerate(
            _array(item["possible_outcomes"], f"{path}.possible_outcomes")
        )
    )
    return Check(
        action=_text(item["action"], f"{path}.action"),
        reason=_text(item["reason"], f"{path}.reason"),
        basis_refs=_refs(item["basis_refs"], f"{path}.basis_refs", minimum=1),
        possible_outcomes=outcomes,
    )


def _parse_outcome(value: Any, path: str) -> CheckOutcome:
    item = _object(value, path, {"observation", "implication"})
    return CheckOutcome(
        observation=_text(item["observation"], f"{path}.observation"),
        implication=_text(item["implication"], f"{path}.implication"),
    )


def _validate_references(
    result: InvestigationResultV1,
    *,
    source_catalog: SourceCatalog,
    registered_hypothesis_catalog: RegisteredHypothesisCatalog,
) -> None:
    ids: dict[str, str] = {}

    def register(local_id: str, kind: str, path: str):
        if local_id in ids:
            raise ReferenceContractError(
                path, f"duplicate local id {local_id!r}; first used as {ids[local_id]}"
            )
        ids[local_id] = kind

    for index, item in enumerate(result.known_facts):
        register(item.id, "finding", f"$.known_facts[{index}].id")
    for index, item in enumerate(result.evidence):
        register(item.id, "finding", f"$.evidence[{index}].id")
    for index, item in enumerate(result.contradictions):
        register(item.id, "contradiction", f"$.contradictions[{index}].id")
    for index, item in enumerate(result.hypotheses):
        register(item.id, "hypothesis", f"$.hypotheses[{index}].id")

    _validate_source_refs(
        result.problem.source_refs, "$.problem.source_refs", source_catalog
    )
    for index, item in enumerate(result.known_facts):
        _validate_source_refs(
            item.source_refs, f"$.known_facts[{index}].source_refs", source_catalog
        )
    for index, item in enumerate(result.evidence):
        _validate_source_refs(
            item.source_refs, f"$.evidence[{index}].source_refs", source_catalog
        )

    for index, item in enumerate(result.contradictions):
        for ref_index, ref in enumerate(item.finding_refs):
            _require_ref(
                ref,
                ids,
                {"finding"},
                f"$.contradictions[{index}].finding_refs[{ref_index}]",
            )

    registered_refs: set[str] = set()
    for index, item in enumerate(result.hypotheses):
        path = f"$.hypotheses[{index}]"
        if item.origin is HypothesisOrigin.REGISTERED:
            if item.registered_ref not in registered_hypothesis_catalog:
                raise ReferenceContractError(
                    f"{path}.registered_ref",
                    f"unauthorized registered hypothesis {item.registered_ref!r}",
                )
            if item.registered_ref in registered_refs:
                raise ReferenceContractError(
                    f"{path}.registered_ref",
                    f"registered hypothesis {item.registered_ref!r} is already used",
                )
            registered_refs.add(item.registered_ref)
        for field_name, refs in (
            ("supporting_finding_refs", item.supporting_finding_refs),
            ("opposing_finding_refs", item.opposing_finding_refs),
        ):
            for ref_index, ref in enumerate(refs):
                _require_ref(
                    ref,
                    ids,
                    {"finding"},
                    f"{path}.{field_name}[{ref_index}]",
                )

    for index, item in enumerate(result.checks):
        for ref_index, ref in enumerate(item.basis_refs):
            _require_ref(
                ref,
                ids,
                {"finding", "contradiction", "hypothesis"},
                f"$.checks[{index}].basis_refs[{ref_index}]",
            )


def _validate_source_refs(
    refs: Sequence[str], path: str, source_catalog: SourceCatalog
) -> None:
    for index, ref in enumerate(refs):
        if ref not in source_catalog:
            raise ReferenceContractError(
                f"{path}[{index}]", f"unauthorized source reference {ref!r}"
            )


def _require_ref(
    ref: str,
    ids: Mapping[str, str],
    allowed_kinds: set[str],
    path: str,
) -> None:
    kind = ids.get(ref)
    if kind is None:
        raise ReferenceContractError(path, f"unknown local reference {ref!r}")
    if kind not in allowed_kinds:
        raise ReferenceContractError(
            path, f"reference {ref!r} points to disallowed type {kind!r}"
        )


def _object(
    value: Any,
    path: str,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StructuralContractError(path, "expected an object")
    required_fields = frozenset(required)
    allowed_fields = required_fields | frozenset(optional)
    missing = required_fields - set(value)
    if missing:
        raise StructuralContractError(
            path, "missing fields: " + ", ".join(sorted(missing))
        )
    additional = set(value) - allowed_fields
    if additional:
        raise StructuralContractError(
            path, "unexpected fields: " + ", ".join(sorted(additional))
        )
    return value


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise StructuralContractError(path, "expected an array")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise StructuralContractError(path, "expected a string")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        raise StructuralContractError(path, "expected a non-empty string")
    return normalized


def _refs(value: Any, path: str, minimum: int = 0) -> tuple[str, ...]:
    refs = tuple(
        _text(item, f"{path}[{index}]")
        for index, item in enumerate(_array(value, path))
    )
    if len(refs) < minimum:
        raise StructuralContractError(path, f"expected at least {minimum} reference(s)")
    if len(set(refs)) != len(refs):
        raise ReferenceContractError(path, "duplicate references are not allowed")
    return refs


def _local_id(value: Any, path: str, kind: str) -> str:
    local_id = _text(value, path)
    if not _ID_PATTERNS[kind].fullmatch(local_id):
        raise StructuralContractError(
            path, f"expected an id with the {kind}_ prefix"
        )
    return local_id


def _enum(value: Any, path: str, enum_class: type[Enum]):
    text = _text(value, path)
    try:
        return enum_class(text)
    except ValueError:
        allowed = ", ".join(item.value for item in enum_class)
        raise StructuralContractError(path, f"expected one of: {allowed}") from None
