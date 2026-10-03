# FaultTrace — Project Overview

## What FaultTrace is

FaultTrace is a Django application for investigating equipment failures. Its purpose is not to let an LLM diagnose a machine autonomously. It gives maintenance technicians a traceable workspace in which current facts, sourced evidence, earlier incidents, technical documentation, hypotheses, contradictions, and next checks can be kept distinct.

The primary user is a maintenance technician working inside an organization. The technician owns the investigation and the final diagnosis. The Agent may organize information, retrieve authorized material, compare support and opposition, expose uncertainty, and suggest checks. It must not turn history into proof, turn a manual into a diagnosis, invent evidence or sources, or confirm a cause on the technician's behalf.

## Product principles

- Investigation is evidence-led, not answer-led.
- A hypothesis is a possibility until a technician confirms it in the operational domain.
- Provenance must survive retrieval and model processing.
- Tenant boundaries are enforced from `Organization` through queries, forms, services, context construction, and Tools.
- Application-owned validation is authoritative; valid JSON alone is not a valid investigation.
- LLM content and its presentation are separate concerns.
- Invalid raw model output and model reasoning are intentionally ephemeral in the structured experiment.
- Contract validity is not the same as technical correctness. Technical reasoning and check usefulness require human review.

These principles are visible in the domain status `CONFIRMED_BY_TECHNICIAN`, the prompts, source catalogs, opaque references, allow-listed Tools, the local `InvestigationResultV1` validator, and the Phase 4.1 corpus/evaluator.

## Current stack

- Python 3.13 container image
- Django 5.2
- PostgreSQL 17
- Server-rendered Django templates and static CSS
- `pypdf` for textual PDF indexing
- Fernet encryption (`cryptography`) for provider credentials
- Provider SDKs for Mistral, OpenAI, Google Gemini, Anthropic, and Groq
- Markdown rendering for the current legacy Agent response, with model HTML and link syntax escaped first
- Docker Compose for local application/database orchestration
- GitHub Actions with PostgreSQL for checks, migrations, and tests

Deployment architecture, production hardening, performance, and scalability are not confirmed by this repository.

## Django apps and responsibilities

| App | Responsibility |
|---|---|
| `accounts` | Organizations, custom users, roles, admin authorization decorator |
| `assets` | Equipment, uploaded documents, document administration and reindexing |
| `maintenance` | Incidents, investigations, interventions, lifecycle services, current UI workflow |
| `knowledge` | Facts, evidence and provenance, hypotheses and relations, document sections, retrieval |
| `config` | Per-organization encrypted AI provider configuration |
| `agent` | Current legacy Agent plus the separate experimental structured Agent |
| `faulttrace` | Project settings, root URLs, home page and unauthenticated health endpoint |

## Existing functionality

- Authentication through Django's built-in auth views.
- Organization-scoped equipment and document browsing; admins can create/edit equipment and create/reindex documents.
- Text extraction and page-level indexing for textual PDFs associated with equipment.
- Incident creation, investigation start, and resolution through an intervention.
- Registration of facts and sourced evidence.
- Creation and management of hypotheses, including supporting and contradicting evidence.
- Retrieval of equipment history, similar incidents, incident details, equipment context, and relevant document sections.
- Per-organization provider/model/API-key configuration.
- A production-facing legacy investigation Agent with native Tool calling and persisted `AgentInteraction` records.
- An experimental, provider-independent structured contract, authorized context, deterministic source catalog, multi-round orchestrator, Groq/GPT-OSS adapter, bounded retries, and safe validation telemetry.
- A five-case synthetic technical corpus and conservative automatic evaluator; real T1–T5 technical-quality evaluation is not complete.

## State at handoff

The operational Django application and legacy Agent coexist with an experimental structured pipeline. The structured pipeline is not connected to views, templates, persistence, or the legacy Agent.

The post-Tool Groq/GPT-OSS protocol continuity is proven through a real `FinalResultTurn`: native `search_documentation`, authorized retrieval, append-only context expansion, preservation of the assistant `reasoning` field and Tool call ID, and acceptance of the second request. The real R2 scenario has not yet completed as a valid `InvestigationResultV1`; observed final outputs were correctly rejected by semantic/reference validation, and the bounded correction requests then encountered HTTP 429.

Read [HANDOFF.md](HANDOFF.md) before continuing development. Detailed boundaries are in [ARCHITECTURE.md](ARCHITECTURE.md), [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md), and [INVESTIGATION_CONTRACT_V1.md](INVESTIGATION_CONTRACT_V1.md).
