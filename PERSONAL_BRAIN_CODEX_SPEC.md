# Personal Brain — Codex Implementation Spec

**Version:** 1.0 | **Status:** Implementation-ready design; not a claim that code exists | **Language:** English for code/contract clarity | **Product UI:** Traditional Chinese first, extensible i18n.

> This file is the single source of truth for Codex. Read only the sections relevant to the current task. Never implement all phases in one prompt. The MVP is deliberately smaller than the full target architecture.

## 0. Product Contract & Scope

A personal, source-grounded knowledge workspace: user submits a public Instagram Reel URL, uploads a PDF, or supplies a Threads URL/text; the system normalizes source content, extracts traceable evidence, creates an editable note with claims, and lets the user explicitly initiate bounded research. It may proactively **suggest** research opportunities but **must not** automatically start paid deep research.

### MVP (P0)
1. Supabase email auth and strict per-user isolation.
2. Asset inbox: PDF upload; IG Reel URL; Threads URL with user-provided text/manual paste fallback. Display source, status, errors, and provenance.
3. Async ingest through durable DB queue and Python worker; parsers produce normalized source + evidence locators. Reel: permitted retrieval, audio transcription, scene-change-based selected keyframes and visual description. PDF: extract selectable text and pages; scanned PDF explicitly marked unsupported or routed to optional OCR later. Threads: public content where permitted, otherwise user-supplied text. No stealth login, bypassing protections, or scraping private content.
4. Source Note generated with claim-level citations; edit and save note; source click-through (PDF page, Reel timestamp, Threads URL where available).
5. Manual Research: user chooses question, plan is bounded, evidence retrieval, source comparison, Research Draft with citations and uncertainty; controlled publish as `research_note`.
6. User-visible progress, errors, cancel, retry/resume where safe; usage and cost limits.
7. Minimal research suggestions: low-cost event signals + daily ranking, user manually starts research. UI may launch after core ingest/research is stable (P1 rollout within overall product roadmap).

### Not MVP (P2+)
Facebook, YouTube, automated news crawling, personalized ML ranker, autonomous paid research, full dynamic DAG engine, distributed microservices, external graph DB, elaborate recommendation feeds, mobile app, multi-agent supervisor, complex social features, automatic verification of every claim, unrestricted OCR, universal social-platform coverage.

### Acceptance definition
A user can upload a normal text PDF or submit an accessible Reel, obtain an editable note with citations linked to exact source locations, request research with a hard per-job cost budget, recover from worker restart without losing committed progress, and see truthful partial/unsupported states. Unsupported social content must fail gracefully with manual-input fallback; no fabricated transcript.

## 1. Locked ADRs — Do Not Re-litigate

**ADR-001 Assets**: stable `asset_id`; reprocessing stages a new revision then atomically promotes; retain last valid revision on failure. Delete Asset/source media/derived evidence per retention policy; independent Notes remain with `source_deleted` citation markers. Reel original video/audio temporary on worker and deleted after processing; retain transcript, parsed text, selected keyframes until Asset deletion. Uploaded original PDFs retained privately.

**ADR-002 Evidence/Claims**: Note-local independently verifiable Claims, selective extraction, conditions/time/uncertainty retained; claim-level citations; DB lightweight evidence index + Storage full artifacts. `claim_type` separates source assertion, forecast, inference, hypothesis. Verification states `unreviewed`, `insufficient_evidence`, `partially_supported`, `corroborated`, `disputed`, `outdated`; independent `needs_review` flag. Evidence relation `asserts`, `supports`, `contradicts`, `contextualizes`, `mentions`; assertion is not verification. Event/manual reverification; tiered cheap screening vs deeper checks. Budgets: **$10/month research variable spend**, **$0.50 per Research Job**, **$0.50/day total paid automatic verification + opportunity work**; shared auto cap, not each agent. Fixed infrastructure excluded; manual research not subject to the daily *automatic* cap, but is subject to job/month limits. Budget exhaustion pauses/checkpoints.

**ADR-003 Knowledge Graph**: PostgreSQL relational topics/relations, optional pgvector. AI topic proposals match existing first; user can edit/rename/merge/split; two-level presentation with many-to-many topic relations. Topic assignment: Asset candidates → Note refinement → Claim specificity as useful. Claim relations evidence-gated; types `similar_to`, `equivalent_to`, `related_to`, `supports`, `contradicts`, `depends_on`, `causes`; causal links only proposed without stronger verification. Event-triggered lightweight discovery, deep during Research. Research produces Draft then controlled publish to shared `notes` table with `note_type` (`source_note`, `research_note`, `manual_note`). No AI-on-AI circular citation; never silently overwrite user edits.

**ADR-004 Opportunities**: suggestion-only; events trigger cheap signals, daily dedup/rank. Rule scoring with limited LLM shortlist review; text + semantic dedup, LLM only if needed; graduated cooldown. Threshold **>=60/100**, max **10 NEW suggestions/day**, backlog unbounded. Opportunity lifecycle separate from Research Job. Preference learning explicit signals + cautious behavioral signals, retain cross-topic exploration. Research Plan with subquestions, evidence requirements, budget and stop rules; hard caps and quality stopping. Suggested score weights (tunable, NOT confirmed fixed constants): relevance 30%, novelty 25%, gap/conflict 20%, researchability 15%, value 10%. Suggested cooldown durations (NOT confirmed): skip 7d, not-interested 30d, completed 30d absent material new evidence.

**ADR-005 Orchestration**: modular handlers on initial single Render Python Worker. PostgreSQL durable queue, atomic claim, lease, heartbeat, fencing generation; at-least-once execution + idempotent effects, not exactly-once execution. Error-classified retry, exponential backoff+jitter, no retry for permanent errors. DB checkpoint metadata + Storage artifacts; stage-resume. Unified Jobs + typed versioned payload + Attempts; fixed workflow templates + dependency DAG, reject cycles. Priority classes + aging + resource-aware concurrency, fairness. Atomic budget reservation before paid calls, settlement, immutable ledger, uncertain-charge reconciliation. Horizontal scaling later. Suggested lease 120s/heartbeat 30s are tunable only.

**ADR-006 Defaults (assistant-selected to avoid further decision rounds; not user-approved ADR)**: hybrid retrieval = PostgreSQL full-text + pgvector + topic/claim graph candidates; embeddings for evidence chunks, claims, note summaries; multi-factor rank with deliberate contradictory evidence retrieval. Implement FTS + metadata first, add vector and graph retrieval behind interfaces once justified. Do not let relevance score imply truth.

## 2. Architecture & Boundaries

- **Frontend/API**: Next.js App Router + TypeScript on Vercel; authenticated request validation, job creation, list/read/edit, signed access. API routes never execute long media/LLM pipelines.
- **Database/Auth**: Supabase Postgres + Supabase Auth; per-user RLS for all exposed data, private Storage buckets, no service-role key in browser. Security-definer DB RPC only where required, fixed `search_path`, explicit auth/worker checks.
- **Worker**: Python on Render background worker; handler registry `ingest_pdf`, `ingest_reel`, `ingest_threads`, `generate_note`, `extract_claims`, `rank_opportunities`, `plan_research`, `execute_research`, `publish_research`. Can begin with fewer handlers if preserving typed interfaces.
- **Storage**: private Supabase Storage for originals and immutable revisioned normalized artifacts, transcript JSON, keyframes, checkpoints, research outputs. DB stores stable IDs, object keys, hashes, mime, size, revision, provenance.
- **LLM/tools**: provider adapters; use cheap deterministic preprocessing before paid models. Model, prompt and tool versions recorded for reproducibility. No hard-coded model/vendor assumptions.
- **Trust**: fetched content is untrusted evidence, not instructions; strip/ignore prompt injection in PDFs, captions and pages. SSRF protection on URL ingest; allowlist schemes, block private/loopback/link-local/metadata IPs after redirects and DNS resolution, size/time limits. Restrict permitted downloads; don't circumvent access controls.

## 3. Suggested Repository Layout (adapt existing repo; do not rewrite unnecessarily)

```text
apps/web/
  app/(app)/inbox/  app/(app)/assets/[id]/  app/(app)/notes/[id]/
  app/(app)/research/  app/api/{assets,jobs,notes,research}/
  lib/{auth,db,api,validation}/
services/worker/
  src/personal_brain/{runner,queue,budget,storage,models}/
  src/personal_brain/handlers/{pdf,reel,threads,note,claims,research,opportunity}/
  src/personal_brain/retrieval/{fts,vector,graph,rank}/
  tests/
supabase/migrations/
packages/contracts/  # JSON schemas or TS/Python shared contract fixtures
spec/PERSONAL_BRAIN_CODEX_SPEC.md
```

If starting from an existing project: inspect files first; make minimum necessary changes. Avoid duplicate auth, queue, storage, retrieval modules.

## 4. Core Data Model (conceptual SQL contract)

All tables with user-owned records require `user_id uuid not null` and tested RLS. Primary keys UUID; `created_at`/`updated_at` UTC. Define FK deletion explicitly. DB indexes on user_id + hot filters, not speculative indexes everywhere.

| Table | Required core columns / invariant |
|---|---|
| `assets` | id,user_id,source_type,source_url nullable,source_external_id nullable,status,active_revision_id nullable,created_at; stable across reprocessing |
| `asset_revisions` | id,asset_id,revision_no,fingerprint,processor_version,status,normalized_object_key,created_at; promote only validated revision transactionally |
| `asset_artifacts` | id,revision_id,kind,object_key,sha256,mime,size_bytes,retention_class; immutable versioned keys |
| `asset_evidence` | id,revision_id,locator_type,locator_json,excerpt,source_hash,content_object_key nullable; PDF page + offsets, Reel start/end seconds + frame IDs, Threads post/url + offsets |
| `notes` | id,user_id,note_type,title,body,body_revision,publication_state,created_at,updated_at; edits do not overwrite source artifacts |
| `note_assets` | note_id,asset_id,relationship; many-to-many |
| `claims` | id,note_id,user_id,text,claim_type,verification_status,needs_review,source_note_revision,created_at |
| `claim_evidence` | claim_id,evidence_id,relation_type,source_revision_id,validity_status; source deletion marks invalid, not silently remapped |
| `topics` | id,user_id,canonical_name,aliases,display_parent_id nullable; user edits authoritative |
| `asset_topics`,`note_topics`,`claim_topics` | typed join tables with confidence/source where useful |
| `topic_relations` | source_topic_id,target_topic_id,relation_type; no rigid tree restriction |
| `claim_relations` | source_claim_id,target_claim_id,relation_type,status,reasoning_summary,created_by,verified_at; provenance required |
| `claim_relation_evidence` | claim_relation_id,evidence_id,relation_type |
| `research_opportunities` | id,user_id,question,score,status,signal_types,topic_ids,semantic_key,cooldown_until,latest_evidence_at; dedup within user |
| `opportunity_events` | opportunity_id,event_type,user_id,metadata,created_at; feedback history |
| `research_runs` | id,user_id,opportunity_id nullable,question,plan_json,status,job_id,output_note_id nullable; repeated runs allowed |
| `research_outputs` | id,research_run_id,object_key,kind,created_at |
| `jobs` | id,user_id,job_type,payload_json,payload_schema_version,status,priority,resource_class,idempotency_key,attempt_count,max_attempts,next_run_at,lease_owner,lease_expires_at,lease_generation,checkpoint_ref,cancel_requested_at,created_at,updated_at |
| `job_attempts` | id,job_id,attempt_no,lease_generation,worker_id,started_at,ended_at,error_class,error_code |
| `job_dependencies` | job_id,depends_on_job_id,dependency_policy; no cycles; same-user dependency |
| `job_checkpoints` | id,job_id,stage,artifact_ref,artifact_hash,processor_version,created_at |
| `job_events` | id,job_id,event_type,metadata,created_at; immutable audit |
| `budget_accounts` | user_id,monthly_research_limit_usd,per_job_research_limit_usd,daily_auto_limit_usd |
| `budget_periods` | user_id,scope,period_start,period_end,spent_usd,reserved_usd; unique scope/period |
| `budget_reservations` | id,user_id,job_id nullable,request_key,scope,amount_reserved_usd,amount_settled_usd,status,expires_at; unique request key |
| `usage_ledger` | id,user_id,job_id nullable,reservation_id,provider,model,estimated_or_actual,amount_usd,usage_json,created_at; append-only |
| `embedding_records` | id,user_id,entity_type,entity_id,content_hash,model_version,vector,created_at; enable only in vector phase |

**Critical integrity rules**: do not publish half-completed revision; do not cascade-delete user Notes when Asset deleted; invalidate evidence/citations on source deletion/revision change; user-edited note revisions invalidate dependent claim verification as appropriate; `research_note` cannot cite itself or downstream AI summaries as independent primary sources. Use DB constraints and transaction boundaries, not only app validation.

## 5. Typed Contracts

```ts
type SourceType = 'pdf'|'instagram_reel'|'threads';
type JobStatus = 'queued'|'running'|'retry_wait'|'paused_budget'|'paused_user'|'completed'|'failed'|'cancelled';
type NoteType = 'source_note'|'research_note'|'manual_note';
type EvidenceLocator =
 | {type:'pdf'; page:number; startOffset?:number; endOffset?:number; bbox?:number[]}
 | {type:'video'; startSec:number; endSec:number; frameIds?:string[]}
 | {type:'post'; url?:string; postId?:string; startOffset?:number; endOffset?:number};
interface NormalizedAsset {
  schemaVersion: 1; assetId:string; revisionId:string; sourceType:SourceType;
  title?:string; sourceUrl?:string; language?:string;
  segments: Array<{id:string; text:string; locator:EvidenceLocator; artifactRefs?:string[]}>;
  provenance:{retrievedAt:string; processorVersion:string; sourceFingerprint:string};
  warnings:string[];
}
interface ResearchPlan {
  question:string; scope?:string; subquestions:string[];
  evidenceRequirements:string[]; maxCostUsd:number;
  maxQueries:number; maxRounds:number; stopConditions:string[];
}
```

Validate all external payloads (Zod / Pydantic or equivalent); version and test serialized contracts. `maxQueries` and `maxRounds` must be finite positive numbers before a job runs.

## 6. Workflow Templates & State Machines

**PDF**: validate/upload original → extract native text/pages → normalize/evidence → generate source note → selective claim extraction → candidate topics → publish editable draft. Scanned-only input: explicit warning; optional OCR later.

**Reel**: validate public URL/permissions → retrieve permitted media to temp disk → extract audio → transcribe and segment timestamps; select keyframes based on meaningful scene change with dedup/caps → describe selected frames → fuse transcript+visual evidence → normalize/evidence → note/claims/topics → delete temp media. Avoid one screenshot per frame; cap media size, duration, frames, model calls. Retrieval may fail; offer manual transcript/upload if legally/technically appropriate.

**Threads**: attempt permitted public text retrieval → normalize with post locator → note/claims; if blocked, ask user to paste text or supply export; never invent missing content.

**Research**: user clicks Start → create `research_run` + plan with finite limits → reserve budget per paid call → retrieve local evidence (FTS first) + permitted web sources → inspect source independence and contradiction → synthesize sourced findings with uncertainty → draft → user-controlled publish. Plan execution is authorized by user click, not by background recommendation event.

**Opportunity**: source/claim/topic event → cheap candidate signals → daily dedup/rank → if score >=60, recommend up to 10 new/day → explicit feedback/cooldown → user clicks to research. Repeated events update existing opportunity, not duplicate cards.

**Job transitions**: `queued -> running -> completed`; transient failure `running -> retry_wait -> queued`; cost cap `running -> paused_budget`; user pause `running -> paused_user`; permanent error `running -> failed`; cancellation request -> terminal `cancelled` after safe stop. Ensure no illegal transition, stale lease commit, or child release before successful dependencies. Mark blocked descendants according to dependency policy.

## 7. Queue, Claim, Retry & Checkpoint Protocol

1. Create job and dependencies transactionally with per-user idempotency key. Use unique constraints scoped by intended operation; do not prevent deliberate reruns with new keys.
2. Claim through a single transaction / guarded RPC with `FOR UPDATE SKIP LOCKED`, eligibility checks (`next_run_at`, dependencies, priority/aging, resource capacity), set `lease_owner`, `lease_expires_at`, increment `lease_generation`, increment attempt count.
3. Heartbeat extends lease only if `job_id + lease_owner + lease_generation + running` match. DB clock is authoritative.
4. Every state-changing commit and artifact publication validates current fencing generation; stage immutable artifacts first, then commit DB pointer in transaction.
5. Worker crash: expired lease becomes reclaimable; idempotent stages use content hash + processor version + revision fingerprint. External calls may duplicate; budget reservations must be unique per logical paid request and uncertain calls reconciled conservatively.
6. Retry only transient errors, exponential backoff+jitter and bounded attempts. Permanent invalid source/unsupported format/permission issue requires user intervention. Budget exhaustion is pause, not failure.
7. Persist checkpoint after meaningful costly stage. DB stores stage and immutable artifact reference; Storage stores payload. On resume validate source revision, hash and handler version.
8. Cancel: API persists cancellation immediately; worker checks between stages and before paid calls; best-effort abort active subprocess/tool. Never promise instant cancellation of already submitted API calls.
9. Scheduling: user-initiated work higher priority; aging avoids starvation; per-user fairness; resource class concurrency tokens for media, lightweight tasks and research. No speculative AI-based scheduling.

## 8. Budget Protocol — Non-negotiable

Budget accounting uses **decimal USD**, not floating-point money. All limits enforced at DB transaction boundary, across workers. Define timezone for daily periods (user timezone, default Asia/Taipei), UTC storage; monthly reset follows configured billing calendar policy (initial default calendar month). Monthly scope includes research variable spend; daily automatic scope includes paid automatic verification and opportunity processing. Record exactly which requests consume each scope.

Before each billable tool/model request: calculate conservative upper bound from model rates, input tokens, capped output tokens and search fees; atomically reserve against applicable `job`, `monthly`, and if automatic `daily_auto` budgets. Reservation is one logical request with multiple applicable scope debits, not independent duplicate charges. If insufficient, pause with checkpoint and user-visible reason. After response, settle actual or conservative estimate, release unused reserve, append ledger. On timeout/unknown billing, mark `uncertain`, do not blindly release. Reconcile provider usage where possible. Bound tool outputs and `max_output_tokens`. **Provider billing can lag; limits are best-effort hard gates, not a guarantee of exact invoice caps.**

## 9. Evidence, Retrieval & Citation

- Normalized segments carry immutable revision + locator + content hash. DB indexes excerpt and location; large transcript/PDF/keyframes in private Storage.
- First retrieval stage: tenant-filtered FTS, topic/entity filters, freshness, claim links. Optional phase adds pgvector and graph expansion; reciprocal-rank fusion or simple explainable score. Only hydrate top relevant segments.
- Chunk boundaries: PDF semantic paragraphs within pages; Reel timestamped utterance blocks and scene changes; Threads post/thread segments. Never lose page/time locator across chunking. Keep overlap small, configurable; don't embed every video frame.
- Embed evidence chunks, claim text, note summaries when vector phase enabled; cache by content hash + model version; revision invalidation is explicit.
- Rank by relevance, source quality, recency when applicable, independence and contradiction coverage; never equate ranking with verification.
- Citation format internally: `{asset_id, revision_id, evidence_id, locator, validity_status}`. Clicking citation resolves authorized source to PDF page or Reel timestamp; deleted sources display `source_deleted`, not broken silent citations.
- Claim verification requires independent evidence where feasible. If no source, report `insufficient_evidence`. Preserve contradictory sources, uncertainty and dates.

## 10. Security, Privacy & Retention

- All user-owned rows RLS tested with two distinct users; Storage private and scoped; signed URLs short-lived. Backend service-role only in protected worker/API env and never logged or sent to client. Avoid broad `SECURITY DEFINER` exposure.
- User uploads: MIME sniff + extension checks, size limits, PDF parser sandbox/resource limits; scan/sanitize as appropriate. URL ingest SSRF prevention including redirects. Social platform access must follow available permissions and constraints.
- Redact secrets and user content from logs. Avoid storing social login credentials. No browser-password automation in this product.
- Reel original video/audio deleted from temp on success/failure; transcript/keyframes retained privately until Asset deletion. Original uploaded PDFs retained until deletion. Note persists independently of source. Implement actual Storage cleanup plus DB tombstone/citation invalidation with retryable deletion jobs; document minimal audit metadata retention.
- Data export/delete endpoints can be P1, but schema must support them from day one.

## 11. API Surface (minimum)

| Method | Route | Behavior |
|---|---|---|
| POST | `/api/assets` | create URL/manual-text Asset, enqueue ingest idempotently |
| POST | `/api/assets/upload` | authorize signed upload and register PDF metadata |
| GET | `/api/assets` | paginated tenant-scoped inbox |
| GET | `/api/assets/:id` | detail, status, evidence preview |
| POST | `/api/assets/:id/reprocess` | stage new revision; never destroy current on failure |
| DELETE | `/api/assets/:id` | request source deletion and citation invalidation |
| GET/PATCH | `/api/notes/:id` | read/edit note with optimistic revision check |
| POST | `/api/research` | explicit user action; bounded plan/job creation |
| GET | `/api/research/:id` | plan, progress, draft, citations |
| POST | `/api/research/:id/publish` | controlled publish; no overwrite of existing user notes |
| GET | `/api/opportunities` | suggestions and reasons, optional later phase |
| POST | `/api/opportunities/:id/feedback` | save/snooze/dismiss/start action |
| GET | `/api/jobs/:id` | status, stage, safe error summary, cost |
| POST | `/api/jobs/:id/cancel` | persist cancellation; cooperative stop |
| POST | `/api/jobs/:id/resume` | authorized and budget-gated resume |

Use auth + ownership checks on every endpoint; Zod request schemas; predictable error codes; pagination; no public direct Storage paths.

## 12. UX Minimum

Inbox: submit URL, PDF upload, source badges, queued/running/partial/failed/completed status, manual fallback for inaccessible sources. Asset view: summary, transcript/text, visual observations, evidence click-through. Notes: editable title/body, Claim cards with citation and verification labels, source deletion warnings. Research: question, plan/limits, progress, costs, pause/cancel, Draft and publish. Opportunities: reason, related evidence, score, snooze/dismiss/start; clearly mark suggestion vs completed research. Never show fabricated availability or falsely label inferred content as source quotes.

## 13. Delivery Phases — Strict Scope Gates

**Phase 0: Repo Audit & Baseline** — inspect existing stack, package scripts, DB migrations, auth, storage, deployment. Report reusable components (including any existing RAG/PDF parser), gaps, risk. No refactor before approval. Add minimal architecture docs and tests. Deliver small PR.

**Phase 1: Foundation** — migrations for Assets/Revisions/Artifacts/Evidence, Notes, Jobs/Attempts/Dependencies/Checkpoint, RLS, private buckets, basic API, worker queue claim/heartbeat/fencing. Prove two-user isolation and restart recovery. Defer research/graph/vector.

**Phase 2: Source Ingestion** — PDF native-text path, manual text/Threads fallback, Reel adapter with graceful inaccessible-source errors; normalized contract + locators + storage + note generation. Validate 3 fixtures per source where possible. Avoid OCR and private-site automation.

**Phase 3: Claims & Research MVP** — claim extraction, evidence citation UI, FTS retrieval, explicit research plan/job, budget reservation/settlement, draft/publish. Prove $0.50 job cap in concurrency tests. No autonomous research.

**Phase 4: Opportunities** — event signals, daily rank/dedup, score>=60 and <=10 new/day, feedback and cooldown, shared $0.50/day auto cap. Can ship behind feature flag.

**Phase 5: Retrieval & Scale** — pgvector embeddings, graph relation candidates, hybrid rank, index revision handling, independent source evaluation, worker pool split only when observed load requires it.

**Phase 6: Polish** — export/delete flows, OCR if justified, observability, performance, accessibility, i18n, additional sources.

Each phase ends with tests, migration rollback notes, measured cost/performance, and user review before proceeding.

## 14. Tests & Acceptance Gates

- **Unit**: parser fixture normalization, PDF page locator, Reel timestamps/keyframe dedup, claim selective extraction schemas, retry classification, budget math, status transitions, idempotency.
- **DB integration**: two users cannot access each other's rows/storage; atomic claim race (2+ workers only 1 active lease); expired lease reclaimed; old generation cannot commit; duplicate job key returns same logical job; DAG cycles rejected; dependency failure blocks downstream.
- **Resilience**: crash after artifact upload before DB promotion; resume from valid checkpoint; stale content hash forces recompute; reprocess failure retains active revision; cancellation during LLM call does not publish.
- **Budget**: concurrent reservations cannot oversubscribe DB budget; auto daily scope shared across recommendation/verification; manual research exempt only from auto-daily cap; uncertain provider timeout does not refund prematurely; ledger append-only.
- **Evidence**: claim citation resolves to exact PDF page/video seconds; deleting Asset invalidates citations but retains Note; no AI-only self-citation; conflicting evidence displayed.
- **E2E**: PDF upload→note→edit→research draft→publish; accessible Reel→transcript/keyframes→timestamp citation; inaccessible Reel/Threads→honest error/manual fallback.
- **Performance**: paginated lists, bounded evidence hydration, bounded media frame count and token usage; benchmark before optimization.

## 15. Observability & Operations

Structured logs with `request_id`, `job_id`, `attempt_id`, `asset_id`, `user_id` as opaque IDs only; stage durations, error class, provider cost estimates, queue depth, lease expiration/reclaims, retry count, checkpoint resume, DB/storage errors. Never log raw secrets, private transcripts or full prompts by default. Admin-only health endpoint and safe stuck-job dashboard. DB backups and migration rollback procedure documented. Test Render shutdown/redeploy semantics and worker signal handling before production.

## 16. Codex Token-Efficient Execution Contract

**Never ask Codex to implement this whole spec in one turn.** Put this file at `spec/PERSONAL_BRAIN_CODEX_SPEC.md` and a concise `AGENTS.md` at repo root. Codex should:

1. Read `AGENTS.md`, relevant spec headings, and files in the targeted module only; use `rg` to locate symbols before opening files. Do not recursively dump repo, logs, PDFs, node_modules, or generated artifacts.
2. Before editing, provide **max 8 lines**: goal, impacted paths, dependencies, test command, risks. Ask only if a blocking product decision is truly unresolved.
3. Change only one bounded vertical slice; avoid unrelated formatting, dependency upgrades, renames, or broad refactors. Reuse existing auth/RAG/parsers where contracts permit.
4. Run targeted tests first; run full suite only at phase gate. Do not repeatedly rerun unchanged commands. No live external API calls in default tests.
5. Final answer **max 12 lines**: changed files, behavior, tests + pass/fail, migrations/env vars, known limitations, next smallest task. No full-file code paste, no verbose command logs.
6. If task would require >~8 files or a schema+UI+worker redesign, split into 2–4 subtasks before coding.
7. Keep a compact `docs/IMPLEMENTATION_STATUS.md` with phase, completed items, blockers, next action, decisions changed. Read it instead of long chat history.
8. Never claim tests/deployments were run if not executed. Never apply destructive migrations, production credentials or paid API calls without explicit authorization.

### Suggested `AGENTS.md` (copy verbatim)

```md
# Codex Working Rules
- Source of truth: spec/PERSONAL_BRAIN_CODEX_SPEC.md; preserve locked ADR-001..005.
- MVP first; no unsolicited architecture expansion or unrelated refactor.
- Read only task-relevant spec sections and files; use rg before opening.
- One vertical slice per task; state impacted paths and test plan in <=8 lines.
- Use Supabase RLS, private Storage, typed payloads, durable queue, fenced commits.
- Never initiate paid deep research automatically; reserve budget before paid calls.
- Cite evidence by immutable revision and locator; never invent source content.
- Run targeted tests; report results and changed paths in <=12 lines.
- Update docs/IMPLEMENTATION_STATUS.md with concise state and next action.
- No destructive migrations or live paid calls without explicit approval.
```

### Prompt 0 — Audit only (start here)

```text
Read AGENTS.md and spec/PERSONAL_BRAIN_CODEX_SPEC.md sections 0, 1, 2, 3, 13, 16 only. Audit the existing repository for reusable auth, DB, Storage, RAG, PDF/Reel adapters, worker and tests. Do not edit code. Output: (1) actual repo map, (2) reusable modules and gaps, (3) risks, (4) Phase 1 broken into <=4 PR-sized tasks, (5) recommended first task. <=600 words; cite file paths. Do not inspect large binary artifacts or unrelated directories.
```

### Prompt 1 — First bounded implementation

```text
Read AGENTS.md, docs/IMPLEMENTATION_STATUS.md (if present), and spec sections 4, 5, 13, 14, 16. Implement ONLY Phase 1 task 1 as identified in the audit, preserving existing code. Before editing list affected files and focused tests. No UI redesign or external paid API. Run targeted tests and summarize <=12 lines; update implementation status. Stop after this task.
```

### Prompt template — Every subsequent task

```text
Task: <one narrowly scoped acceptance criterion>.
Read AGENTS.md, docs/IMPLEMENTATION_STATUS.md, and ONLY spec sections <IDs> plus relevant files. Preserve ADR decisions. Implement minimal change, add targeted tests, run them, update status. Do not touch unrelated modules or call paid APIs. Stop after task and report <=12 lines.
```

## 17. Open Implementation Parameters (not blockers)

Set conservative defaults via env/config and benchmark: max PDF bytes/pages, Reel duration/media size, frame sampling threshold and cap, chunk size/overlap, embedding model/dimensions, retrieval top-k, max search queries/rounds, lease and heartbeat, worker concurrency, retry attempts, opportunity cooldown days, preference weights, monthly budget reset timezone. All must have bounded defaults before production, with tests; no need to ask user to choose each value.

## 18. Non-negotiable Product Truthfulness

- A source stating a claim is not independent verification.
- Research completed does not mean question conclusively answered.
- Missing or blocked social content is an error/fallback, not permission to fabricate.
- Citation to deleted or revised source must show invalidation.
- AI outputs cannot recursively corroborate themselves.
- A budget check without atomic reservation is not sufficient.
- A worker lease alone does not guarantee exactly-once external side effects.
- Future-phase functionality must not be represented as implemented.
