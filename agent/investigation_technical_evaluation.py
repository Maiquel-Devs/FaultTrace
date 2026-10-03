"""Deterministic and human-review boundaries for the Phase 4.1 corpus."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .investigation_context import InvestigationContext
from .investigation_contracts import HypothesisOrigin, InvestigationResultV1
from .investigation_technical_corpus import TechnicalEvaluationCase


class EvaluationVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class CriterionEvaluation:
    verdict: EvaluationVerdict
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AutomaticEvaluation:
    schema: CriterionEvaluation
    refs: CriterionEvaluation
    fact_fidelity: CriterionEvaluation
    confirmation: CriterionEvaluation
    hypotheses: CriterionEvaluation
    contradictions: CriterionEvaluation
    checks: CriterionEvaluation
    human_review: CriterionEvaluation
    invented_facts_detected: bool
    confirmation_error_detected: bool


def evaluate_automatic(
    case: TechnicalEvaluationCase,
    context: InvestigationContext,
    result: InvestigationResultV1,
    *,
    locator_aliases: Mapping[str, str] | None = None,
) -> AutomaticEvaluation:
    """Evaluate only observable properties; technical merit stays REVIEW."""

    authorized_refs = set(context.consulted_source_refs)
    cited_refs = _cited_source_refs(result)
    invalid_refs = sorted(cited_refs - authorized_refs)
    refs = _criterion(not invalid_refs, "Todas as refs existem.", invalid_refs)

    locator_refs = {
        source.internal_locator: source.ref for source in context.sources
    }
    locator_aliases = locator_aliases or {}
    missing_locators = [
        locator
        for locator in case.expectations.must_cite_locators
        if locator_aliases.get(locator, locator) not in locator_refs
        or locator_refs[locator_aliases.get(locator, locator)] not in cited_refs
    ]
    factual_text = _factual_text(result)
    missing_facts = [
        fact
        for fact in case.expectations.must_preserve_facts
        if _normalize(fact) not in _normalize(factual_text)
    ]
    invented_numbers = sorted(
        _numbers(factual_text) - _numbers(_authorized_text(context))
    )
    fact_issues = []
    if missing_locators:
        fact_issues.append(f"Fontes obrigatórias não citadas: {missing_locators}")
    if missing_facts:
        fact_issues.append(f"Fatos comparáveis não preservados literalmente: {missing_facts}")
    if invented_numbers:
        fact_issues.append(f"Números factuais ausentes do contexto: {invented_numbers}")

    complete_text = json.dumps(result.to_dict(), ensure_ascii=False)
    forbidden = [
        phrase
        for phrase in case.expectations.must_not_assert
        if _normalize(phrase) in _normalize(complete_text)
    ]
    confirmation = _criterion(
        not forbidden,
        "Nenhuma afirmação de confirmação explicitamente proibida foi encontrada.",
        forbidden,
    )

    unacceptable = [
        item.evidence_assessment.value
        for item in result.hypotheses
        if item.evidence_assessment
        not in case.expectations.acceptable_assessments
    ]
    proposed_as_facts = [
        item.statement
        for item in result.hypotheses
        if item.origin is HypothesisOrigin.PROPOSED
        and _normalize(item.statement) in _normalize(factual_text)
    ]
    hypothesis_issues = []
    if unacceptable:
        hypothesis_issues.append(f"Assessments fora do aceitável: {unacceptable}")
    if proposed_as_facts:
        hypothesis_issues.append(
            f"Hipóteses PROPOSED repetidas como fatos: {proposed_as_facts}"
        )

    contradiction_ok = (
        len(result.contradictions)
        >= case.expectations.minimum_contradictions
    )
    checks_ok = len(result.checks) >= case.expectations.minimum_checks
    human_reasons = (
        *case.expectations.expected_reasoning_properties,
        *case.expectations.expected_check_properties,
        "Plausibilidade técnica, rationale, significance e segurança operacional exigem revisão humana.",
    )
    return AutomaticEvaluation(
        schema=CriterionEvaluation(
            EvaluationVerdict.PASS,
            ("Resultado é uma instância validada de InvestigationResultV1.",),
        ),
        refs=refs,
        fact_fidelity=CriterionEvaluation(
            EvaluationVerdict.FAIL if fact_issues else EvaluationVerdict.PASS,
            tuple(fact_issues) or ("Cobertura factual determinística atendida.",),
        ),
        confirmation=confirmation,
        hypotheses=CriterionEvaluation(
            EvaluationVerdict.FAIL if hypothesis_issues else EvaluationVerdict.PASS,
            tuple(hypothesis_issues) or ("Separação estrutural de hipóteses atendida.",),
        ),
        contradictions=CriterionEvaluation(
            EvaluationVerdict.PASS if contradiction_ok else EvaluationVerdict.FAIL,
            (
                f"Contradições: {len(result.contradictions)}; mínimo esperado: "
                f"{case.expectations.minimum_contradictions}."
            ,),
        ),
        checks=CriterionEvaluation(
            EvaluationVerdict.PASS if checks_ok else EvaluationVerdict.FAIL,
            (
                f"Checks: {len(result.checks)}; mínimo esperado: "
                f"{case.expectations.minimum_checks}."
            ,),
        ),
        human_review=CriterionEvaluation(EvaluationVerdict.REVIEW, human_reasons),
        invented_facts_detected=bool(invented_numbers),
        confirmation_error_detected=bool(forbidden),
    )


def _criterion(ok: bool, success: str, failures: list[str]) -> CriterionEvaluation:
    return CriterionEvaluation(
        EvaluationVerdict.PASS if ok else EvaluationVerdict.FAIL,
        (success,) if ok else tuple(str(item) for item in failures),
    )


def _cited_source_refs(result: InvestigationResultV1) -> set[str]:
    refs = set(result.problem.source_refs)
    for item in (*result.known_facts, *result.evidence):
        refs.update(item.source_refs)
    return refs


def _factual_text(result: InvestigationResultV1) -> str:
    return " ".join(
        (
            result.problem.statement,
            result.summary,
            *(item.statement for item in result.known_facts),
            *(item.statement for item in result.evidence),
        )
    )


def _authorized_text(context: InvestigationContext) -> str:
    return " ".join(source.authorized_content for source in context.sources)


def _numbers(value: str) -> set[str]:
    return set(re.findall(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])", value))


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(normalized.split())
