"""Authorized context boundary for future structured investigation producers.

The current Agent does not import this module.  It converts application-owned
source candidates into deterministic, execution-local opaque references and
keeps internal locators out of the producer representation.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .investigation_contracts import (
    InvestigationResultV1,
    RegisteredHypothesisCatalog,
    SourceCatalog,
    SourceCatalogEntry,
)


class ContextBuildError(ValueError):
    """Raised when authorized context inputs are internally inconsistent."""


class SourceKind(str, Enum):
    CURRENT_INCIDENT = "CURRENT_INCIDENT"
    EQUIPMENT = "EQUIPMENT"
    INVESTIGATION = "INVESTIGATION"
    FACT = "FACT"
    EVIDENCE = "EVIDENCE"
    HISTORICAL_INCIDENT = "HISTORICAL_INCIDENT"
    INTERVENTION = "INTERVENTION"
    DOCUMENT = "DOCUMENT"
    DOCUMENT_SECTION = "DOCUMENT_SECTION"


@dataclass(frozen=True)
class SourceCandidate:
    """Application-owned source before an opaque reference is assigned."""

    internal_locator: str
    kind: SourceKind
    authorized_content: str
    display_label: str

    def __post_init__(self):
        object.__setattr__(
            self,
            "internal_locator",
            _text(self.internal_locator, "source.internal_locator"),
        )
        if not isinstance(self.kind, SourceKind):
            raise ContextBuildError("source.kind must be a SourceKind value")
        object.__setattr__(
            self,
            "authorized_content",
            _text(self.authorized_content, "source.authorized_content"),
        )
        object.__setattr__(
            self,
            "display_label",
            _text(self.display_label, "source.display_label"),
        )


@dataclass(frozen=True)
class RegisteredHypothesisCandidate:
    """Read-only snapshot input for a hypothesis already registered in the domain."""

    internal_locator: str
    statement: str
    observed_status: str
    supporting_source_locators: tuple[str, ...] = ()
    opposing_source_locators: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(
            self,
            "internal_locator",
            _text(self.internal_locator, "hypothesis.internal_locator"),
        )
        object.__setattr__(
            self, "statement", _text(self.statement, "hypothesis.statement")
        )
        object.__setattr__(
            self,
            "observed_status",
            _text(self.observed_status, "hypothesis.observed_status"),
        )
        object.__setattr__(
            self,
            "supporting_source_locators",
            _locator_tuple(
                self.supporting_source_locators,
                "hypothesis.supporting_source_locators",
            ),
        )
        object.__setattr__(
            self,
            "opposing_source_locators",
            _locator_tuple(
                self.opposing_source_locators,
                "hypothesis.opposing_source_locators",
            ),
        )
        overlap = set(self.supporting_source_locators) & set(
            self.opposing_source_locators
        )
        if overlap:
            raise ContextBuildError(
                "registered hypothesis source relations must be disjoint"
            )


@dataclass(frozen=True)
class AuthorizedSource:
    ref: str
    kind: SourceKind
    authorized_content: str
    display_label: str
    internal_locator: str

    def to_producer_dict(self) -> dict[str, str]:
        return {
            "ref": self.ref,
            "kind": self.kind.value,
            "content": self.authorized_content,
        }


@dataclass(frozen=True)
class RegisteredHypothesisSnapshot:
    ref: str
    statement: str
    observed_status: str
    supporting_source_refs: tuple[str, ...]
    opposing_source_refs: tuple[str, ...]
    internal_locator: str

    def to_producer_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "statement": self.statement,
            "observed_status": self.observed_status,
            "supporting_source_refs": list(self.supporting_source_refs),
            "opposing_source_refs": list(self.opposing_source_refs),
        }


@dataclass(frozen=True)
class InvestigationContext:
    sources: tuple[AuthorizedSource, ...]
    registered_hypotheses: tuple[RegisteredHypothesisSnapshot, ...]

    @property
    def source_catalog(self) -> SourceCatalog:
        return SourceCatalog(
            SourceCatalogEntry(
                ref=source.ref,
                kind=source.kind.value,
                display_label=source.display_label,
            )
            for source in self.sources
        )

    @property
    def registered_hypothesis_catalog(self) -> RegisteredHypothesisCatalog:
        return RegisteredHypothesisCatalog(
            hypothesis.ref for hypothesis in self.registered_hypotheses
        )

    @property
    def consulted_source_refs(self) -> tuple[str, ...]:
        """Sources made available to the producer in deterministic order."""

        return tuple(source.ref for source in self.sources)

    def cited_source_refs(
        self, result: InvestigationResultV1
    ) -> tuple[str, ...]:
        """Derive cited refs in first-citation order without duplicates."""

        authorized = set(self.consulted_source_refs)
        ordered: list[str] = []
        ref_groups = [
            result.problem.source_refs,
            *(item.source_refs for item in result.known_facts),
            *(item.source_refs for item in result.evidence),
        ]
        for refs in ref_groups:
            for ref in refs:
                if ref not in authorized:
                    raise ContextBuildError(
                        f"result cites a source outside this context: {ref!r}"
                    )
                if ref not in ordered:
                    ordered.append(ref)
        return tuple(ordered)

    def resolve_source(self, ref: str) -> AuthorizedSource:
        for source in self.sources:
            if source.ref == ref:
                return source
        raise KeyError(ref)

    def resolve_registered_hypothesis(
        self, ref: str
    ) -> RegisteredHypothesisSnapshot:
        for hypothesis in self.registered_hypotheses:
            if hypothesis.ref == ref:
                return hypothesis
        raise KeyError(ref)

    def to_producer_dict(self) -> dict[str, object]:
        """Return only data authorized for a future structured-output producer."""

        return {
            "sources": [source.to_producer_dict() for source in self.sources],
            "registered_hypotheses": [
                hypothesis.to_producer_dict()
                for hypothesis in self.registered_hypotheses
            ],
        }

    def to_producer_json(self) -> str:
        return json.dumps(self.to_producer_dict(), ensure_ascii=False)


def build_investigation_context(
    *,
    sources: Iterable[SourceCandidate],
    registered_hypotheses: Iterable[RegisteredHypothesisCandidate] = (),
) -> InvestigationContext:
    """Build deterministic execution-local refs from application identities."""

    source_candidates = tuple(sources)
    source_by_locator: dict[str, SourceCandidate] = {}
    for source in source_candidates:
        if not isinstance(source, SourceCandidate):
            raise TypeError("sources must contain SourceCandidate values")
        if source.internal_locator in source_by_locator:
            raise ContextBuildError(
                f"duplicate source locator {source.internal_locator!r}"
            )
        source_by_locator[source.internal_locator] = source

    ordered_sources = sorted(
        source_candidates,
        key=lambda item: (item.kind.value, item.internal_locator),
    )
    authorized_sources = tuple(
        AuthorizedSource(
            ref=f"src_{index}",
            kind=source.kind,
            authorized_content=source.authorized_content,
            display_label=source.display_label,
            internal_locator=source.internal_locator,
        )
        for index, source in enumerate(ordered_sources, start=1)
    )
    source_ref_by_locator = {
        source.internal_locator: source.ref for source in authorized_sources
    }
    source_kind_by_locator = {
        source.internal_locator: source.kind for source in authorized_sources
    }

    hypothesis_candidates = tuple(registered_hypotheses)
    hypothesis_by_locator: dict[str, RegisteredHypothesisCandidate] = {}
    for hypothesis in hypothesis_candidates:
        if not isinstance(hypothesis, RegisteredHypothesisCandidate):
            raise TypeError(
                "registered_hypotheses must contain "
                "RegisteredHypothesisCandidate values"
            )
        if hypothesis.internal_locator in hypothesis_by_locator:
            raise ContextBuildError(
                f"duplicate hypothesis locator {hypothesis.internal_locator!r}"
            )
        hypothesis_by_locator[hypothesis.internal_locator] = hypothesis

    snapshots = []
    for index, hypothesis in enumerate(
        sorted(hypothesis_candidates, key=lambda item: item.internal_locator),
        start=1,
    ):
        supporting = _resolve_evidence_locators(
            hypothesis.supporting_source_locators,
            source_ref_by_locator,
            source_kind_by_locator,
            "supporting_source_locators",
        )
        opposing = _resolve_evidence_locators(
            hypothesis.opposing_source_locators,
            source_ref_by_locator,
            source_kind_by_locator,
            "opposing_source_locators",
        )
        snapshots.append(
            RegisteredHypothesisSnapshot(
                ref=f"ctx_hypothesis_{index}",
                statement=hypothesis.statement,
                observed_status=hypothesis.observed_status,
                supporting_source_refs=supporting,
                opposing_source_refs=opposing,
                internal_locator=hypothesis.internal_locator,
            )
        )

    return InvestigationContext(
        sources=authorized_sources,
        registered_hypotheses=tuple(snapshots),
    )


def extend_investigation_context(
    context: InvestigationContext,
    *,
    sources: Iterable[SourceCandidate] = (),
    registered_hypotheses: Iterable[RegisteredHypothesisCandidate] = (),
) -> InvestigationContext:
    """Append authorized inputs without changing refs already issued.

    Repeated, identical candidates are idempotent. Reusing an application
    locator with different authorized data is rejected because silently
    replacing it would change the meaning of an already-issued reference.
    """

    if not isinstance(context, InvestigationContext):
        raise TypeError("context must be an InvestigationContext")

    existing_sources = {
        source.internal_locator: source for source in context.sources
    }
    new_sources: list[SourceCandidate] = []
    seen_new_sources: dict[str, SourceCandidate] = {}
    for candidate in sources:
        if not isinstance(candidate, SourceCandidate):
            raise TypeError("sources must contain SourceCandidate values")
        existing = existing_sources.get(candidate.internal_locator)
        if existing:
            if not _source_matches_candidate(existing, candidate):
                raise ContextBuildError(
                    "source locator is already bound to different authorized data"
                )
            continue
        duplicate = seen_new_sources.get(candidate.internal_locator)
        if duplicate:
            if duplicate != candidate:
                raise ContextBuildError(
                    "duplicate source locator has conflicting authorized data"
                )
            continue
        seen_new_sources[candidate.internal_locator] = candidate
        new_sources.append(candidate)

    next_source_number = _next_ref_number(
        (source.ref for source in context.sources), "src_"
    )
    appended_sources = tuple(
        AuthorizedSource(
            ref=f"src_{index}",
            kind=candidate.kind,
            authorized_content=candidate.authorized_content,
            display_label=candidate.display_label,
            internal_locator=candidate.internal_locator,
        )
        for index, candidate in enumerate(
            sorted(
                new_sources,
                key=lambda item: (item.kind.value, item.internal_locator),
            ),
            start=next_source_number,
        )
    )
    combined_sources = context.sources + appended_sources
    ref_by_locator = {
        source.internal_locator: source.ref for source in combined_sources
    }
    kind_by_locator = {
        source.internal_locator: source.kind for source in combined_sources
    }

    existing_hypotheses = {
        hypothesis.internal_locator: hypothesis
        for hypothesis in context.registered_hypotheses
    }
    new_hypotheses: list[RegisteredHypothesisCandidate] = []
    seen_new_hypotheses: dict[str, RegisteredHypothesisCandidate] = {}
    for candidate in registered_hypotheses:
        if not isinstance(candidate, RegisteredHypothesisCandidate):
            raise TypeError(
                "registered_hypotheses must contain "
                "RegisteredHypothesisCandidate values"
            )
        supporting = _resolve_evidence_locators(
            candidate.supporting_source_locators,
            ref_by_locator,
            kind_by_locator,
            "supporting_source_locators",
        )
        opposing = _resolve_evidence_locators(
            candidate.opposing_source_locators,
            ref_by_locator,
            kind_by_locator,
            "opposing_source_locators",
        )
        existing = existing_hypotheses.get(candidate.internal_locator)
        if existing:
            if not _hypothesis_matches_candidate(
                existing, candidate, supporting, opposing
            ):
                raise ContextBuildError(
                    "hypothesis locator is already bound to different snapshot data"
                )
            continue
        duplicate = seen_new_hypotheses.get(candidate.internal_locator)
        if duplicate:
            if duplicate != candidate:
                raise ContextBuildError(
                    "duplicate hypothesis locator has conflicting snapshot data"
                )
            continue
        seen_new_hypotheses[candidate.internal_locator] = candidate
        new_hypotheses.append(candidate)

    next_hypothesis_number = _next_ref_number(
        (item.ref for item in context.registered_hypotheses),
        "ctx_hypothesis_",
    )
    appended_hypotheses = []
    for index, candidate in enumerate(
        sorted(new_hypotheses, key=lambda item: item.internal_locator),
        start=next_hypothesis_number,
    ):
        appended_hypotheses.append(
            RegisteredHypothesisSnapshot(
                ref=f"ctx_hypothesis_{index}",
                statement=candidate.statement,
                observed_status=candidate.observed_status,
                supporting_source_refs=_resolve_evidence_locators(
                    candidate.supporting_source_locators,
                    ref_by_locator,
                    kind_by_locator,
                    "supporting_source_locators",
                ),
                opposing_source_refs=_resolve_evidence_locators(
                    candidate.opposing_source_locators,
                    ref_by_locator,
                    kind_by_locator,
                    "opposing_source_locators",
                ),
                internal_locator=candidate.internal_locator,
            )
        )

    return InvestigationContext(
        sources=combined_sources,
        registered_hypotheses=(
            context.registered_hypotheses + tuple(appended_hypotheses)
        ),
    )


def _source_matches_candidate(
    source: AuthorizedSource, candidate: SourceCandidate
) -> bool:
    return (
        source.kind is candidate.kind
        and source.authorized_content == candidate.authorized_content
        and source.display_label == candidate.display_label
    )


def _hypothesis_matches_candidate(
    snapshot: RegisteredHypothesisSnapshot,
    candidate: RegisteredHypothesisCandidate,
    supporting_refs: tuple[str, ...],
    opposing_refs: tuple[str, ...],
) -> bool:
    return (
        snapshot.statement == candidate.statement
        and snapshot.observed_status == candidate.observed_status
        and snapshot.supporting_source_refs == supporting_refs
        and snapshot.opposing_source_refs == opposing_refs
    )


def _next_ref_number(refs: Iterable[str], prefix: str) -> int:
    numbers = []
    for ref in refs:
        if not ref.startswith(prefix):
            raise ContextBuildError(
                f"existing reference {ref!r} does not use {prefix!r}"
            )
        suffix = ref[len(prefix) :]
        if not suffix.isdigit():
            raise ContextBuildError(f"existing reference {ref!r} is malformed")
        numbers.append(int(suffix))
    return max(numbers, default=0) + 1


def _resolve_evidence_locators(
    locators: tuple[str, ...],
    ref_by_locator: dict[str, str],
    kind_by_locator: dict[str, SourceKind],
    field_name: str,
) -> tuple[str, ...]:
    refs = []
    for locator in locators:
        if locator not in ref_by_locator:
            raise ContextBuildError(
                f"{field_name} contains unknown source locator {locator!r}"
            )
        if kind_by_locator[locator] is not SourceKind.EVIDENCE:
            raise ContextBuildError(
                f"{field_name} must point to EVIDENCE sources"
            )
        refs.append(ref_by_locator[locator])
    return tuple(refs)


def _locator_tuple(values: Iterable[str], path: str) -> tuple[str, ...]:
    result = tuple(_text(value, path) for value in values)
    if len(result) != len(set(result)):
        raise ContextBuildError(f"{path} contains duplicate locators")
    return result


def _text(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ContextBuildError(f"{path} must be a string")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        raise ContextBuildError(f"{path} must not be empty")
    return normalized
