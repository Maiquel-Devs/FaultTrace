# FaultTrace — AI Development Handoff

## Read this first

This file is for a new AI or developer taking over without access to prior conversations.

Before altering the project:

1. Read [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md).
2. Read [ARCHITECTURE.md](ARCHITECTURE.md).
3. Read [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md).
4. Read [INVESTIGATION_CONTRACT_V1.md](INVESTIGATION_CONTRACT_V1.md).
5. Read this handoff to the end.
6. Inspect `git status`, the current diff, and recent history before editing.
7. Run the test suite and Django/migration checks.
8. Do not replace the legacy Agent yet.
9. Do not weaken the local validator to make a model output pass.

The code is the primary authority. `PHASE_4_1_REPORT.md` and `phase41_results.json` are historical experimental snapshots and are not the canonical current state.

## Product intent

FaultTrace is an equipment-failure investigation platform, not an autonomous diagnostic chatbot. The technician owns the conclusion. The Agent's role is to organize sourced facts and evidence, retrieve authorized history/documentation, identify support and contradictions, express uncertainty, and recommend useful next checks.

History is a clue, not proof. Documentation supplies meaning and procedures, not automatic causal confirmation. Proposed hypotheses are allowed; invented facts, sources, measurements, or events are not.

## Current state

**Current phase:** Phase 4.1 — real provider validation and technical quality evaluation.

**Checkpoint commit:** `1315ab6` (`feat: consolida validação experimental do agente estruturado`). The commit contains the reasoning round-trip fix, safe validation-failure observability, Phase 4.1 corpus/evaluator/runner, tests, and the historical report/results snapshot. Documentation in `docs/` is intentionally left uncommitted for review at this handoff.

### Proven

- The operational Django domain, organization scoping, retrieval, provider configuration, and legacy Agent are implemented and covered by tests.
- `InvestigationResultV1` exists, parses locally, enforces strict structure, validates refs and semantic relationships, and serializes canonically.
- Authorized context, `SourceCatalog`, registered-hypothesis catalog, opaque refs, and allow-listed projections exist.
- Initial source ordering is deterministic; context expansion is append-only and preserves old refs.
- ORM/retrieval results are reauthorized against the root incident's organization/equipment before structured context expansion.
- `FakeStructuredProducer` and the multi-round orchestrator work deterministically.
- Native Groq Tool calling with `openai/gpt-oss-120b` works.
- In real R2 runs, the model requested `search_documentation`, the application executed authorized retrieval, and the new document section was appended (`src_1`–`src_4` became `src_1`–`src_5`).
- The complete expanded context reached the second producer round.
- GPT-OSS assistant `reasoning` and the native Tool call ID are preserved in memory during the round-trip.
- After that fix, real second-round requests were accepted and produced `FinalResultTurn`. **The post-Tool round-trip is proven.**
- The validator rejects invalid semantic/reference relations rather than accepting parseable JSON.
- Safe validation telemetry records round, attempt, category, sanitized path, and canonical instruction without retaining raw output.
- The five synthetic corpus fixtures and automatic/human evaluation boundary exist.
- No structured result is persisted and no structured renderer/UI integration exists.

### Not yet proven

- Real R2 ending in a valid `InvestigationResultV1`.
- A valid R2 result citing the newly retrieved `src_5`.
- A real validation correction retry completing successfully; observed correction rounds hit HTTP 429.
- Technical quality for T1–T5.
- Model reliability sufficient for operational rollout.
- A persistence design for validated V1 results.
- A semantic renderer controlled by the application.
- An experimental structured UI or compatibility/migration strategy.
- Production deployment, scale, availability, backup, or formal security properties.

## Two Agent worlds

The incident UI calls `maintenance.views.agent_investigate`, which constructs `InvestigationAgent` from `agent/core.py`. That path produces textual Markdown and persists `AgentInteraction`.

The structured files (`investigation_*`, including the orchestrator and Groq producer) are an experimental parallel path. They are invoked by tests and the Phase 4.1 experiment runner, not by views. Never infer that adding the structured classes implicitly changed production behavior.

See [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md) for both flows and their limits.

## Real failures and their meaning

### HTTP 400 after Tool — resolved protocol defect

GPT-OSS emitted `reasoning` on the assistant Tool-call message. The adapter reconstructed the assistant message without it, and Groq rejected the second request. `GroqStructuredInvestigationProducer._map_response` now retains non-empty reasoning in `_messages`; the matching Tool message retains `tool_call_id`. Later real runs reached final output, so this specific continuity defect is fixed and externally exercised.

Do not persist or log the reasoning. Its preservation is protocol state only.

### Semantic invariant rejection — contract held

Observed metadata:

```text
SEMANTIC_INVARIANT_FAILURE
$.hypotheses[0]
Correct the reported relationship while preserving the supplied evidence.
```

The possible hypothesis invariants at that path are support/opposition overlap, `MIXED` missing one side, `LEANS_SUPPORTING` missing support, or `LEANS_OPPOSING` missing opposition. They were reviewed as semantically coherent and were not weakened. The safe metadata cannot distinguish which one occurred because raw output/value is intentionally absent and the canonical instruction is shared.

### Reference rejection — contract held

Observed metadata:

```text
REFERENCE_FAILURE
$.checks[0].basis_refs[0]
Use only references present in the supplied authorized context.
```

The model supplied a relationship that could not resolve to an allowed result-local fact/evidence/contradiction/hypothesis. The exact invalid value is intentionally unavailable.

### Rate limits — external constraint

Correction calls received HTTP 429; the one allowed transport retry also received 429. The execution stopped. Do not increase retries, sleep, or request budget merely to obtain a passing run.

## Phase 4.1 corpus

Fixtures live in `agent/investigation_technical_corpus.py`.

| Case | Domain | Input and trap | Expected evaluation focus |
|---|---|---|---|
| T1 | Electric motor | 1480 rpm and vibration 7.8 mm/s vs historical 3.1; bearing hypothesis | Strong support may lean supporting, but must not confirm; propose a discriminating vibration/bearing check |
| T2 | Hydraulic system | Local gauge 145 bar vs PLC transmitter 92 bar | Preserve both readings, declare conflict, avoid choosing a winner, use mixed/insufficient evidence, request an independent comparison |
| T3 | PLC | One I/O event and explicitly absent voltage/connector/temperature data | Do not fill gaps; keep the contact hypothesis insufficient; collect missing evidence |
| T4 | Thermal oven | Earlier open-relay cause vs current relay command and 18.2 A | Treat history as a clue, recognize current opposition/contradiction, do not copy the prior diagnosis |
| T5 | Pneumatic dryer | E17; manual defines differential pressure and a safe verification procedure | Use documentation as context, not proof; preserve isolate/depressurize condition in a useful check |

Every `TechnicalEvaluationCase` contains `model_input` and a separate `expectations` object. The experiment builds domain fixtures/context from `model_input`; expectations go only to `evaluate_automatic` after a validated result exists. Tests guard that separation.

Automatic evaluation checks observable properties: validated V1 instance, cited refs, required source coverage, literal comparable facts, numbers absent from authorized context, explicitly prohibited assertions, allowed assessment choices, proposed hypotheses duplicated as facts, and minimum contradiction/check counts. It returns human `REVIEW` for plausibility, rationale, significance, evidence weight, check utility, language, and operational safety. “No automatic inconsistency detected” is not “technically correct.”

## Next step

Do not redesign the architecture and do not repeat R2 indefinitely. When provider quota is available, run T1, T2, T3, T4, and T5 once each initially, in that order, stopping at the first sustained HTTP 429. Record schema/ref validity, validation failures and retries, invented facts, confirmation errors, uncertainty, rationale, checks, tokens/requests/duration when available, and retain no invalid raw output.

Then conduct explicit human review of each valid result, especially technical rationale and whether checks reduce uncertainty safely. Only after this evidence should the project decide whether it is ready for persistence design.

The existing `run_phase41_experiment` function currently sequences R2 before T1–T5. Do not invoke it blindly if the approved goal is corpus-only; either obtain approval to use its current sequence or make a separately reviewed, minimal change to target the corpus. That is future work, not performed by this documentation task.

## Future roadmap (direction, not approval)

1. **Complete Phase 4.1 corpus:** one exploratory real run per case plus human review.
2. **Persistence design:** persist only locally validated `InvestigationResultV1`, include schema version and safe execution metadata, keep invalid raw/reasoning ephemeral.
3. **Semantic renderer:** application selects sections/tables/blocks; model controls semantic content, not presentation markup.
4. **Experimental UI:** show structured results to technicians without replacing the current path.
5. **Compatibility/migration strategy:** define coexistence between legacy `AgentInteraction` and structured results.
6. **Rollout:** only after reliability, safety, migration, and operational review.

## DO NOT DO WITHOUT RE-EVALUATING THE ARCHITECTURE

- Do not replace the legacy Agent yet.
- Do not connect the experimental structured flow directly to current views/templates.
- Do not persist invalid/raw LLM output.
- Do not persist model reasoning.
- Do not remove `SourceCatalog` or registered-hypothesis catalogs.
- Do not expose arbitrary database primary keys/internal locators to the model.
- Do not renumber/recalculate established refs during an execution.
- Do not weaken or silently extend `InvestigationResultV1`.
- Do not remove semantic invariants to improve pass rate.
- Do not create open-ended validation or transport retries.
- Do not turn historical similarity into proof of the current cause.
- Do not turn documentation into automatic diagnosis.
- Do not execute textual claims as Tools; only native, allow-listed, validated Tool calls are executable.
- Do not trust a result merely because its JSON parses or matches a provider schema.
- Do not equate contract validity with technical correctness.
- Do not treat `PHASE_4_1_REPORT.md` or `phase41_results.json` as current truth without comparing them to code and newer evidence.

## Environment variables

Names are defined in `.env.example`, `faulttrace/settings.py`, and `docker-compose.yml`:

| Variable | Required / default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | Required | Django cryptographic secret |
| `DJANGO_DEBUG` | Optional, defaults false in settings/Compose | Debug mode |
| `DJANGO_ALLOWED_HOSTS` | Optional, defaults `localhost,127.0.0.1` | Host allow-list |
| `POSTGRES_DB` | Required | Database name |
| `POSTGRES_USER` | Required | Database user |
| `POSTGRES_PASSWORD` | Required | Database password |
| `POSTGRES_HOST` | Optional, defaults `db` | Database host |
| `POSTGRES_PORT` | Optional, defaults `5432` | Database port |
| `AI_CREDENTIAL_ENCRYPTION_KEY` | Setting defaults empty; operationally required for AI credential save/decrypt | URL-safe Fernet key |

Provider API keys are entered per organization in the application and encrypted in the database. There is no Groq key environment variable in the application settings. Never copy `.env`, API keys, admin passwords, or headers into documentation/logs.

## Operational commands

### Local Docker workflow

```powershell
Copy-Item .env.example .env
# Replace example secrets in .env, then:
docker compose up --build
```

Application: `http://localhost:8000/`  
Health: `http://localhost:8000/health/`  
Login: `http://localhost:8000/accounts/login/`

The source is copied into the image. Rebuild after source/template/static changes:

```powershell
docker compose up --build -d web
```

### Validation

```powershell
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py migrate --check
docker compose exec web python manage.py test
git diff --check
git status --short
```

If running Python directly rather than in the container, provide all environment variables and a reachable PostgreSQL instance. Do not run the full suite in parallel with another Django test command against the same configured database; a prior concurrent attempt collided while creating the shared test database. Sequential execution passed.

### Initial local data

Create an organization, then a superuser:

```powershell
docker compose exec web python manage.py shell -c "from accounts.models import Organization; Organization.objects.get_or_create(name='Empresa Exemplo')"
docker compose exec web python manage.py createsuperuser
```

Create the deterministic demo using a locally chosen password (never commit it):

```powershell
docker compose exec web python manage.py seed_demo --password "choose-a-local-password"
```

### Provider experiments

There is deliberately no recommended command here that automatically makes a real provider call. Inspect `agent/phase41_experiment.py`, confirm scope/fixtures/retry policy, obtain an active organization configuration, and explicitly approve the exact experiment before invocation. CI uses mocks/fakes and makes no external LLM calls.

## Test map

- `accounts`, `assets`, `maintenance`, `knowledge`, `config`, and `faulttrace` tests cover tenant scoping, lifecycle, forms/views, retrieval/indexing, credentials, and health.
- `agent/tests.py` covers the legacy Agent, Tool loop, persistence, provider mappings, and safe Markdown.
- `agent/test_investigation_contracts.py` covers V1 parsing, invariants, references, serialization, security, and independence.
- `agent/test_investigation_context.py` and `test_investigation_context_adapter.py` cover catalogs, append-only refs, safe producer representation, authorization, determinism, schema, and failure policy.
- `agent/test_investigation_structured_orchestrator.py` covers multi-round Tools, limits, validation correction, and execution statuses with a fake producer.
- `agent/test_investigation_groq_producer.py` covers Groq mapping, native IDs, `reasoning` preservation, retry/budget/log safety, and mocked integration.
- `agent/test_investigation_technical_evaluation.py` covers fixtures, model-input/expectation separation, conservative evaluation, no persistence, and safe failure telemetry.

At the Phase 4.1 checkpoint, the relevant structured group passed 145 tests and the sequential full suite passed **217 tests**. Re-run tests after any checkout/environment change; the current result is a point-in-time observation, not a permanent guarantee.

## Historical artifacts

`phase41_results.json` is versioned in checkpoint `1315ab6` because it is a sanitized, reproducible historical record of the first experiment sequence and contains no raw result/reasoning/context. It is deliberately not updated to pretend it captured later runs. It reports the old R2 HTTP 400 and T1 HTTP 429 and is stale for current proof status.

`PHASE_4_1_REPORT.md` accompanies that snapshot and is also historical. Its conclusions, test count, and `git status` describe the earlier moment. Preserve it as evidence; do not cite it as the final handoff state without this caveat.

## Final takeover checklist

Before any future change:

- Confirm clean/expected working-tree state and protect user changes.
- Compare requested work with the two-Agent boundary.
- Identify whether a real provider call is explicitly authorized.
- Keep provider experiments bounded and stop on rate limit.
- Keep invalid raw output and reasoning ephemeral.
- Add deterministic tests only for deterministic behavior.
- Run full sequential validation and migration checks.
- Report what is proven separately from what merely appears plausible.
