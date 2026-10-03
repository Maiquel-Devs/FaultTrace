# InvestigationResultV1 — Contract Reference

## Purpose and authority

`InvestigationResultV1` is the provider-independent semantic result for the experimental Agent. Its implementation in `agent/investigation_contracts.py` is authoritative. `agent/investigation_schema.py` is the provider-facing structural description, but successful provider-side formatting never replaces local parsing and validation.

The contract is deliberately separate from Django models, `AgentInteraction`, UI rendering, and the legacy Agent. It is immutable data after construction and currently has no database persistence.

## Validation sequence

`parse_investigation_result_json` performs:

1. JSON parsing; malformed JSON raises `ContractParseError`.
2. Strict structural parsing: object/array/string/enum types, required and extra fields, IDs, non-empty text, and per-field cardinality.
3. Cross-field semantic invariants while parsing hypotheses.
4. Local-ID uniqueness and source/local/registered-hypothesis reference validation.
5. Construction of `InvestigationResultV1` only if every check passes.

Error subclasses distinguish structural, reference, and semantic failures. All model output remains untrusted before this boundary.

## Top-level structure

All eight fields are required and no extra top-level field is accepted.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string literal `"1.0"` | Contract version |
| `problem` | `ProblemStatement` | Problem under investigation and its source |
| `summary` | non-empty string | Concise investigation summary |
| `known_facts` | array of `KnownFact` | Statements treated as supplied/observed facts |
| `evidence` | array of `EvidenceItem` | Evidence and why it matters |
| `contradictions` | array of `Contradiction` | Explicit conflicts between findings |
| `hypotheses` | array of `HypothesisAnalysis` | Registered or newly proposed possibilities |
| `checks` | array of `Check` | Proposed next checks and their discriminating outcomes |

The six arrays may be empty. Whenever an item exists, all its required fields and invariants apply.

## Structures

### ProblemStatement

| Field | Type / constraint |
|---|---|
| `statement` | non-empty string |
| `source_refs` | at least one distinct source ref from the execution's `SourceCatalog` |

### KnownFact

| Field | Type / constraint |
|---|---|
| `id` | unique local ID matching `fact_...` |
| `statement` | non-empty string |
| `source_refs` | at least one distinct authorized `src_N` ref |

### EvidenceItem

| Field | Type / constraint |
|---|---|
| `id` | unique local ID matching `evidence_...` |
| `statement` | non-empty string |
| `significance` | non-empty string |
| `source_refs` | at least one distinct authorized `src_N` ref |

`KnownFact` and `EvidenceItem` are collectively “findings” for local relationships.

### Contradiction

| Field | Type / constraint |
|---|---|
| `id` | unique local ID matching `contradiction_...` |
| `finding_refs` | exactly two distinct local refs; each must resolve to a fact or evidence item |
| `explanation` | non-empty string |
| `implication` | non-empty string |

A contradiction cannot refer directly to a source, hypothesis, check, or another contradiction.

### HypothesisAnalysis

| Field | Type / constraint |
|---|---|
| `id` | unique local ID matching `hypothesis_...` |
| `origin` | `REGISTERED` or `PROPOSED` |
| `statement` | non-empty string |
| `evidence_assessment` | one of the four assessments below |
| `supporting_finding_refs` | distinct local refs, all resolving to facts/evidence |
| `opposing_finding_refs` | distinct local refs, all resolving to facts/evidence |
| `rationale` | non-empty string |
| `registered_ref` | conditionally required opaque `ctx_hypothesis_N` ref |

All local IDs across facts, evidence, contradictions, and hypotheses share one namespace and must be unique.

#### Origin

- `REGISTERED`: represents a hypothesis already present in the authorized investigation context. It **must** include `registered_ref`; that ref must exist in `RegisteredHypothesisCatalog` and may be used by only one result hypothesis.
- `PROPOSED`: a new analytical possibility from the model. It **must not** contain `registered_ref`.

Proposing a hypothesis is not itself hallucination. Presenting that hypothesis as a sourced fact, inventing its provenance, or treating it as confirmed would be a different error.

#### Evidence assessment

- `INSUFFICIENT`: the current evidence does not support a directional conclusion. The code does **not** require support/opposition arrays to be empty.
- `MIXED`: requires at least one supporting and at least one opposing finding.
- `LEANS_SUPPORTING`: requires at least one supporting finding.
- `LEANS_OPPOSING`: requires at least one opposing finding.

For every assessment, the same finding ref is forbidden from appearing in both support and opposition. The contract does not require an opposing ref for `LEANS_SUPPORTING`, does not require a supporting ref for `LEANS_OPPOSING`, and does not automatically judge the technical merit of the rationale.

These invariants are semantic, not formatting conveniences: the directional label must be backed by the corresponding declared relationship, `MIXED` must represent both sides, and a single finding cannot simultaneously occupy both roles for one hypothesis.

### Check

| Field | Type / constraint |
|---|---|
| `action` | non-empty string |
| `reason` | non-empty string |
| `basis_refs` | at least one distinct local ref to a fact, evidence item, contradiction, or hypothesis |
| `possible_outcomes` | array of `CheckOutcome`; may be empty |

A check cannot cite another check or a raw source ref directly as `basis_refs`. It must point into the result's semantic graph.

### CheckOutcome

| Field | Type / constraint |
|---|---|
| `observation` | non-empty string |
| `implication` | non-empty string |

## Reference spaces

The contract deliberately separates three namespaces:

| Namespace | Example | Created by | May be used for |
|---|---|---|---|
| Authorized source | `src_5` | `InvestigationContext` | `problem.source_refs`, fact/evidence `source_refs` |
| Registered hypothesis | `ctx_hypothesis_1` | Authorized context adapter | `REGISTERED.registered_ref` only |
| Result-local semantic object | `fact_speed`, `hypothesis_bearing` | Model, under pattern/uniqueness validation | Contradictions, hypothesis support/opposition, check basis |

Database primary keys are not valid model-facing refs. A source ref must exist in the supplied catalog; a registered ref must exist in its separate catalog; a local ref must resolve to an allowed already-parsed object kind.

## Strict structural rules

- Every object rejects unexpected fields.
- Every required field must be present.
- Text values are strings and must be non-empty after trimming.
- Enum values must match exactly.
- Reference arrays reject duplicates.
- Local IDs must match their type prefix and be globally unique in the result.
- `schema_version` must be exactly `1.0`.
- Serialization through `to_dict`/`to_json` returns the canonical V1 shape and omits `registered_ref` for proposed hypotheses.

## Safe failure taxonomy

| Category | Source | Canonical instruction |
|---|---|---|
| `SYNTAX_FAILURE` | JSON cannot be parsed | `Return one complete JSON object matching InvestigationResultV1.` |
| `STRUCTURAL_CONTRACT_FAILURE` | Shape/type/required/extra/enum/cardinality/ID violation | `Correct the value at the reported contract path; do not add fields.` |
| `REFERENCE_FAILURE` | Unauthorized, unknown, duplicate, or wrong-kind relationship | `Use only references present in the supplied authorized context.` |
| `SEMANTIC_INVARIANT_FAILURE` | Cross-field hypothesis relation violation | `Correct the reported relationship while preserving the supplied evidence.` |

The reference instruction is intentionally generic even when the rejected relation is a result-local ref rather than a `SourceCatalog` ref. The stored safe path supplies structural location but never the invalid value.

## What V1 does not prove

A valid V1 result proves that its JSON, structure, reference graph, and encoded invariants are acceptable. It does not prove mechanical/electrical plausibility, causal correctness, appropriate evidence weight, safety of a procedure, or operational usefulness. Those remain human-review concerns, exercised by the Phase 4.1 corpus.

If a future case reveals information that V1 cannot represent, document the scenario, impact, and expected frequency before changing the contract. Do not weaken or extend V1 merely to increase model pass rate.
