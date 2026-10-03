# FaultTrace — Development History

This is a decision-oriented timeline reconstructed from the current code, tests, reports, and Git history. Phase labels were used at different moments for the operational product and for the later structured-Agent experiment; they are therefore two related sequences, not one perfectly uniform numbering scheme.

## Operational foundation

### Infrastructure

The repository began as a Django/PostgreSQL application with Docker Compose. A custom organization-bound user model was introduced before domain data, avoiding a later auth-model migration. The health endpoint, container startup migration, and basic project checks established a reproducible local/CI baseline.

### Operational domain

Equipment, incidents, investigations, interventions, lifecycle transitions, roles, and organization scoping were added. Transactions and row locks protect investigation start/resolution. The core decision was that the technician, not an AI, owns the confirmed cause and operational result.

### Investigation knowledge

Facts, evidence with explicit provenance, hypotheses, and support/contradiction relations were added. This made investigation state a domain model rather than an undifferentiated chat transcript.

### Retrieval and documents

Document upload, PDF page extraction, equipment association, history search, similar-incident search, and documentation search were added. Retrieval is organization/equipment scoped. The purpose is evidence access, not automatic diagnosis: prior resolutions and manual text are inputs whose relevance must still be reasoned about.

### AI configuration

Per-organization provider/model selection and encrypted credentials were introduced. The repository supports Mistral, OpenAI, Gemini, Anthropic, and Groq in the legacy provider layer. Configuration testing is explicit, and credentials are never stored in source.

### Legacy Agent with real providers

`InvestigationAgent` combined authorized initial context, native provider Tool calling, a five-round limit, application Tools, final Markdown, and persisted `AgentInteraction`. Later work hardened multi-provider Tool-call adaptation, Groq support, error handling, and a deterministic demo scenario.

### Presentation hardening

The UI originally relied on generated Markdown organization. Work on source-section removal, tables, and safe Markdown rendering exposed a deeper architectural issue: an LLM-controlled prose format is difficult to validate and render semantically. Escaping HTML/links and improving tables fixed immediate UI behavior, but did not solve the content/presentation coupling.

## Structured-Agent experimental sequence

### Experimental Phase 1 — semantic representation

The project shifted from “ask for well-formatted Markdown” to “ask for a typed investigation graph.” The application would own validation and later presentation, while the model would provide facts, evidence, contradictions, hypotheses, rationale, and checks.

### Phase 2 — `InvestigationResultV1`

The provider-independent contract, parser, structural rules, reference checks, semantic invariants, serialization, and adversarial tests were added. This established that parsing JSON is insufficient: refs and relationships must also be valid.

### Phase 2.5 — authorized context and catalogs

`InvestigationContext`, `SourceCatalog`, source kinds, registered-hypothesis snapshots, opaque refs, deterministic ordering, and append-only expansion were introduced. This separated internal identity from model-facing provenance and made consulted versus cited sources measurable.

### Phase 2.6 — ORM context adapter

The adapter projected an authorized root incident and related records through explicit field allow-lists. It also added reauthorization of retrieval results and tests for organization/equipment isolation, deterministic refs, and absence of internal locators/PKs from the producer representation.

### Phase 3 — producer boundary and orchestrator

`StructuredInvestigationProducer`, explicit `ToolRequestTurn`/`FinalResultTurn`, a scripted fake producer, the structured Tool allow-list, bounded multi-round orchestration, local parsing/validation, failure categories, and one validation correction opportunity were added. This proved the architecture without depending on a live provider.

### Phase 4 — experimental Groq provider

`GroqStructuredInvestigationProducer` mapped Chat Completions Tool calls while preserving native IDs, using local validation and safe logs. Experiments used `openai/gpt-oss-120b`. Real R1 and R3 scenarios succeeded according to the historical Phase 4 context. R2 correctly requested documentation and expanded context, but its second request initially failed with HTTP 429 in one run and HTTP 400 in another.

The implementation also captured a provider limitation: in this tested Groq flow, native structured output and Tool use were not used together. Tool-bearing calls therefore rely on schema/instructions plus the local parser/validator. Calls without Tools can request JSON Object Mode.

### Phase 4.1 — real round-trip, validation telemetry, and technical corpus

Investigation of the HTTP 400 found that GPT-OSS returned an assistant `reasoning` field during Tool calling. The adapter had preserved content and Tool calls but not that field. Replaying the complete assistant message, including `reasoning`, fixed the protocol continuity defect. Later real executions accepted the post-Tool second request and produced `FinalResultTurn`, proving the round-trip through context expansion.

Those final outputs did not yet become valid V1 results:

- one was rejected as `SEMANTIC_INVARIANT_FAILURE` at `$.hypotheses[0]`;
- another was rejected as `REFERENCE_FAILURE` at `$.checks[0].basis_refs[0]`;
- their correction rounds then encountered HTTP 429, including the one allowed transport retry.

The invalid raw output was intentionally ephemeral. Because the first experiment telemetry lost the classification when a later transport error ended the run, `ValidationFailureAudit` and the Phase 4.1 runner were extended to retain only round, attempt, category, sanitized structural path, and canonical instruction.

A small synthetic corpus was also prepared:

- T1 motor: strong evidence without confirmation;
- T2 hydraulic system: conflicting measurements;
- T3 PLC: insufficient evidence;
- T4 thermal equipment: misleading similar history;
- T5 pneumatic system: documentation as context, not proof.

Fixtures keep model input separate from evaluator-only expectations. The automatic evaluator checks only deterministic properties; technical plausibility, rationale, significance, operational language, and check usefulness remain human review.

## Historical artifact caveat

`PHASE_4_1_REPORT.md` and `phase41_results.json` are committed snapshots from an earlier Phase 4.1 run. They record the pre-fix R2 HTTP 400 and an early T1 rate limit, and the report's 213-test count reflects that moment. They do not include the later safe failure details or the real post-fix runs that reached `FinalResultTurn`. They are retained as experimental history, not current-state authority.

The current code, tests, this documentation set, and Git history take precedence when those snapshots differ.

## Why the current boundary exists

The project did not replace the legacy Agent after proving protocol mechanics because several independent questions remain:

1. Can the real provider repeatedly produce a locally valid structured result?
2. Are those valid results technically useful rather than merely structurally correct?
3. What validated data and safe metadata should be persisted?
4. How should the application render semantics independently of model formatting?
5. How should the legacy and structured paths coexist during migration?

Answering these in order prevents UI/persistence work from freezing an unproven model behavior or encouraging weaker validation.
