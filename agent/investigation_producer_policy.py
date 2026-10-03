"""Provider-neutral failure taxonomy and future retry guidance.

This module does not perform retries or provider calls.  It classifies contract
failures and exposes bounded policy guidance for a later integration phase.
"""

from __future__ import annotations

import re
from enum import Enum

from .investigation_contracts import (
    ContractParseError,
    ReferenceContractError,
    SemanticInvariantError,
    StructuralContractError,
)


class ProducerFailureCategory(str, Enum):
    TRANSPORT_PROVIDER_FAILURE = "TRANSPORT_PROVIDER_FAILURE"
    SYNTAX_FAILURE = "SYNTAX_FAILURE"
    STRUCTURAL_CONTRACT_FAILURE = "STRUCTURAL_CONTRACT_FAILURE"
    REFERENCE_FAILURE = "REFERENCE_FAILURE"
    SEMANTIC_INVARIANT_FAILURE = "SEMANTIC_INVARIANT_FAILURE"


class RetryDisposition(str, Enum):
    RETRYABLE = "RETRYABLE"
    NON_RETRYABLE = "NON_RETRYABLE"
    CONDITIONAL = "CONDITIONAL"


_SAFE_CONTRACT_PATH = re.compile(
    r"^\$(?:(?:\.[A-Za-z_][A-Za-z0-9_]*)|(?:\[\d+\]))*$"
)

SAFE_VALIDATION_INSTRUCTIONS = {
    ProducerFailureCategory.SYNTAX_FAILURE: (
        "Return one complete JSON object matching InvestigationResultV1."
    ),
    ProducerFailureCategory.STRUCTURAL_CONTRACT_FAILURE: (
        "Correct the value at the reported contract path; do not add fields."
    ),
    ProducerFailureCategory.REFERENCE_FAILURE: (
        "Use only references present in the supplied authorized context."
    ),
    ProducerFailureCategory.SEMANTIC_INVARIANT_FAILURE: (
        "Correct the reported relationship while preserving the supplied evidence."
    ),
}


def classify_contract_exception(error: Exception) -> ProducerFailureCategory:
    if isinstance(error, ContractParseError):
        return ProducerFailureCategory.SYNTAX_FAILURE
    if isinstance(error, ReferenceContractError):
        return ProducerFailureCategory.REFERENCE_FAILURE
    if isinstance(error, SemanticInvariantError):
        return ProducerFailureCategory.SEMANTIC_INVARIANT_FAILURE
    if isinstance(error, StructuralContractError):
        return ProducerFailureCategory.STRUCTURAL_CONTRACT_FAILURE
    raise TypeError(f"Unsupported producer error type: {type(error).__name__}")


def recommended_retry(
    category: ProducerFailureCategory,
    *,
    failures_so_far: int,
    transport_status: int | None = None,
    timed_out: bool = False,
) -> RetryDisposition:
    """Describe a bounded Phase 3 retry policy without executing a retry."""

    if failures_so_far < 1:
        raise ValueError("failures_so_far must be at least 1")
    if failures_so_far > 1:
        return RetryDisposition.NON_RETRYABLE

    if category is ProducerFailureCategory.TRANSPORT_PROVIDER_FAILURE:
        if timed_out or transport_status == 429 or (
            transport_status is not None and transport_status >= 500
        ):
            return RetryDisposition.RETRYABLE
        if transport_status is not None and 400 <= transport_status < 500:
            return RetryDisposition.NON_RETRYABLE
        return RetryDisposition.CONDITIONAL

    return RetryDisposition.CONDITIONAL


def safe_validation_feedback(error: Exception) -> dict[str, str]:
    """Return minimal producer feedback without stack traces or context data."""

    category = classify_contract_exception(error)
    feedback = {
        "category": category.value,
        "instruction": SAFE_VALIDATION_INSTRUCTIONS[category],
    }
    path = safe_validation_path(getattr(error, "path", None))
    if path is not None:
        feedback["path"] = path
    return feedback


def safe_validation_path(value: object) -> str | None:
    """Allow only field names and numeric indexes from contract structure."""

    if isinstance(value, str) and _SAFE_CONTRACT_PATH.fullmatch(value):
        return value
    return None
