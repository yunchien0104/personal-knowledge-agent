# Deep Research R2B/R2C LLM Integration

Date: 2026-11-04

Branch: `feat/deep-research-llm-integration`

Baseline: `00d3c15`

## Result

Status: **PARTIAL — Execution-layer replay safety, terminal-response classification, contract boundaries, and Local DB budget verification pass; R2B/R2C still require a successful post-repair real-provider verification.**

R2B and R2C are wired into the Deep Worker path through production OpenAI-compatible adapters. Legacy Planner/R1/R2A/R2E/R2F construction and behavior were not changed. The explicitly authorized verification below made one bounded R2B paid call. It did not call R2C, access hosted Supabase, deploy, push, or merge.

The initial missing model and pricing configuration was supplied on 2026-11-04. The bounded live verification below used `gpt-6.1-sol` with Standard service tier, low reasoning effort, and explicit $2/M input and $10/M output pricing. The configured API key was neither printed nor overwritten.

## Execution-layer repair (2026-11-04)

No paid provider request was made during this repair.

| Root cause | Fix | Regression evidence |
| --- | --- | --- |
| Existing `running` and terminal operations could be returned from `_reserve_start()` and then cross the provider boundary again. | Only the worker that successfully performs `reserved -> running` receives the internal `admitted` status. Existing `running` is reconciliation-required; `failed`, `cancelled`, `abandoned`, and `outcome_unknown` require a newly authorized operation; `completed` requires the durable output. | Parameterized R2B/R2C takeover tests, outcome-unknown retrieval test, and completed-R2C replay test verify zero duplicate provider calls and zero new settlements. |
| Chat response content was parsed before inspecting provider terminal metadata, so 950 reasoning tokens with zero visible JSON and `finish_reason=length` appeared to be invalid JSON. | Parse metadata first. `length`, refusal, content filter, unexpected finish reason, empty content, invalid JSON, timeout, and transport uncertainty now have distinct error kinds while preserving available usage, reasoning tokens, request ID, finish reason, latency, and priced cost. | Transport tests reproduce 950 reasoning / 0 visible / length, refusal, content filter, empty content, invalid JSON, and timeout. Adapter tests prove length/refusal/content-filter/invalid-JSON paths do not consume repair. |
| Repair was selected by broad exception flow. | Explicit repair policy permits one repair only for DTO/Pydantic/backend contract validation. Provider terminal errors and unknown outcomes never repair automatically. The operation preflight reserves initial plus one repair call before either call. | Initial+repair usage aggregation, validation repair, terminal no-repair, and preflight reservation tests pass. |
| R2B offered four internal plus four Web queries but converted only the first four. | Provider DTO now exposes one typed `queries` array of at most four `{source, query}` objects; conversion is one-to-one and never slices. | Four-query boundary converts all four; five queries fail DTO validation. |
| R2C silently stopped after 80 accepted evidence records. | The explicit 80-record boundary now raises `EvidencePayloadLimitError`; coordinator ends `authorization_blocked` before reservation/provider execution. | 81-record regression proves fail-closed behavior with no truncation. |

P0 replay safety is **PASS (offline + Local DB)**. A `reserved` operation is recoverable under the fenced lease; a pre-existing `running` operation is not assumed safe to replay. Real lease takeover remains enforced by the database generation fence, and terminal settlement replay is idempotent.

## Implementation

- `worker/src/personal_brain/llm_client.py`: additive `generate_with_metadata()` returning validated structured output, provider/model, authoritative usage, latency, and request ID. Existing `generate()` delegates to it and preserves its return contract. Provider rejection, timeout, transport failure, and invalid structured response now have distinct error kinds while retaining any available usage/request metadata.
- `worker/src/personal_brain/research/deep_llm.py`: separately configured R2B and R2C adapters, provider-compatible strict schemas, Pydantic validation, one bounded repair, verified pricing, conservative input/output upper-bound reservations, and explicit unknown-outcome errors. Unexpected service tier, cache use, or missing usage fail closed because configured Standard pricing would no longer be authoritative.
- `worker/src/personal_brain/research/deep_planning.py`: aggregates initial/repair usage and cost; marks fallback distinctly and propagates provider failures.
- `worker/src/personal_brain/research/deep_evaluation.py`: model output excludes backend-owned identity and telemetry fields; the backend adds them after validation. Success and fallback are persisted distinctly.
- `worker/src/personal_brain/research/schema_utils.py`: Deep schemas preserve provider-supported string, numeric, and array constraints while the default mode retains Legacy compatibility. R2C scores remain independently enforced by Pydantic and are never coerced.
- `worker/src/personal_brain/research/deep_coordinator.py`: reserves before every R2B/R2C operation, settles actual priced usage, preserves the entire reservation as uncertain when the provider outcome/usage is unknown, records request IDs, and rechecks lease/deadline after calls before persistence.
- `worker/src/personal_brain/runner.py`: injects both production adapters only into the Deep coordinator. Missing model/pricing configuration fails closed. Legacy injection is unchanged.
- `supabase/migrations/20261104002931_enforce_deep_research_daily_budget.sql`: additive daily aggregate budget with Asia/Taipei date boundary; atomic run/daily/monthly reservation and settlement locks. Limits remain $0.50/run, $2/day, $10/month, with $0.10 protected final-stage reserve.
- `.env.example` and `render.yaml`: server-only configuration names; no secret values.

## Configuration

Required shared values: `R2B_R2C_API_BASE_URL`, `R2B_R2C_API_KEY`, `R2B_R2C_PROVIDER`.

Required per adapter: `R2B_MODEL` / `R2C_MODEL`, `R2B_MAX_OUTPUT_TOKENS` / `R2C_MAX_OUTPUT_TOKENS`, `R2B_TIMEOUT_SECONDS` / `R2C_TIMEOUT_SECONDS`, `{R2B|R2C}_PRICING_VERIFIED=true`, `{R2B|R2C}_INPUT_COST_PER_MILLION_USD`, and `{R2B|R2C}_OUTPUT_COST_PER_MILLION_USD`.

Reasoning-model configuration also supports `R2B_R2C_SERVICE_TIER`, `R2B_R2C_PROMPT_CACHE_MODE`, and `{R2B|R2C}_REASONING_EFFORT`. Deep calls use `default` (Standard), `low`, and `explicit` prompt-cache mode with no breakpoints. Disabling implicit cache writes keeps the verified $2/M ordinary-input price authoritative; any unexpected cache usage fails closed as `outcome_unknown`. Candidate completion ceilings are now independently configured as **R2B 2560** and **R2C 3584**; they are conservative test candidates, not a sufficiency claim.

`max_completion_tokens` covers visible output plus reasoning tokens. For the deterministic empty-context fixtures, the byte-conservative preflight (initial + one possible repair) produces:

- R2B: 2 calls × 2560 output tokens, 12,346 total input-token upper bound, **$0.0759** maximum reservation.
- R2C: 2 calls × 3584 output tokens, 18,998 total input-token upper bound, **$0.1097** maximum reservation.
- Combined: **$0.1856**, within the non-final portion of the $0.50 run cap, the $2 daily cap, and the $10 monthly cap. Actual preflight remains payload-dependent; larger R2C evidence must be reserved atomically or is blocked before execution.

## Verification

### MOCK — PASS

```sh
PYTHONPATH=worker/src:. worker/.venv/bin/pytest worker/tests -q \
  --ignore=worker/tests/test_http_server.py \
  --ignore=worker/tests/research/test_atomic_generator.py \
  --ignore=worker/tests/research/test_claim_verifier.py \
  --ignore=worker/tests/research/test_research_generator.py \
  --ignore=worker/tests/research/test_research_pipeline_live.py
```

Latest focused execution-layer result: **70/70 PASS**. Latest broad offline result excluding provider/script-entry modules and the sandbox-blocked loopback HTTP module: **211 passed, 9 subtests passed**. Including the HTTP module before the last two focused additions produced **209 passed, 9 subtests passed, 8 setup failures** because this sandbox denied `127.0.0.1:0` socket bind before assertions. Coverage includes terminal response classification, metadata/cost preservation, error-directed repair, replay prevention, lease takeover behavior, query/evidence bounds, reserve-before-call order, settlement, Worker injection, and Legacy separation.

### Provider Schema/DTO/Contract repair — PASS (offline)

- `ProposedRequirement.evidenceType` and `EvidenceRequirementV1.evidenceType` now consume one shared closed Literal: `fact`, `comparison`, `trend`, `counter_evidence`, `primary_source`, or `other`. The observed value `Internal specifications and acceptance criteria` is rejected by the Provider DTO before plan conversion.
- `ProposedAssumption.confidence` and `ResearchAssumptionV1.confidence` now share the same `low` / `medium` / `high` Literal.
- R2B constraint descriptions, dependency keys, and internal/Web query items now carry the same item-level pattern and length constraints required by the objects created during conversion.
- R2C top-level evidence-quality, coverage, subquestion, conflict, gap, and explanation bounds now mirror `EvidenceAssessmentV1`; all nested response objects continue to use the authoritative contract classes directly. Backend-owned identity and telemetry remain excluded from model output.
- The Deep-only Provider schema preserves enums, nullable `anyOf` branches, string/array/numeric bounds, patterns, and supported formats. Every object has all properties in `required` and `additionalProperties=false`. The default/Legacy schema mode remains unchanged.
- Regression tests compare every preserved constraint path between each R2B/R2C Pydantic DTO and the emitted Strict Structured Outputs schema, plus exact enum and top-level contract parity. No Provider call was made during this repair.

### LOCAL_DB — PASS

The repair verification used a newly created disposable project `pb-r2bc-verify-ptq3of` on port 57322 with a unique Docker volume created at 2026-11-04T01:30:27Z. Existing local projects on ports 5432x and 5532x were inventoried and untouched. Only the isolated database service was started. It was stopped with `--no-backup`; its container and volume were confirmed absent afterward.

```sh
supabase start --workdir /tmp/pb-r2bc-verify-ptQ3Of \
  -x gotrue,realtime,storage-api,imgproxy,kong,mailpit,postgrest,postgres-meta,studio,edge-runtime,logflare,vector,supavisor
supabase test db --workdir /tmp/pb-r2bc-verify-ptQ3Of
supabase test db --workdir /tmp/pb-r2bc-verify-ptQ3Of \
  /tmp/pb-r2bc-verify-ptQ3Of/supabase/tests/deep_research_llm_budget.test.sql \
  /tmp/pb-r2bc-verify-ptQ3Of/supabase/tests/deep_research_budget_concurrency.test.sql \
  /tmp/pb-r2bc-verify-ptQ3Of/supabase/tests/deep_research_durability.test.sql
supabase db lint --workdir /tmp/pb-r2bc-verify-ptQ3Of --level error
```

Results:

- Clean migration replay: **33/33 migrations**.
- pgTAP: **19 files, 508/508 assertions PASS**.
- Focused real-PostgreSQL budget/durability/concurrency run: **3 files, 54/54 assertions PASS**. The R2B/R2C file is **15/15 PASS** and now uses the proposed $0.0422/$0.0565 reservations. It covers reserve-before-start, settlement, unused-reservation release, ledger events, and run/daily/monthly balances. The other files verify two actual PostgreSQL sessions, duplicate settlement fencing, lease takeover, and conservative `outcome_unknown` accounting.
- DB lint: **0 errors**.

The schema-repair regression additionally replayed all 33 migrations in a new disposable project `pb-schema-regression-6etlab` on port 58322, then ran the complete database suite: **19 files, 508/508 assertions PASS**. The stack was stopped with `--no-backup`; existing projects on 5432x and 5532x remained running and untouched. No database migration changed in this repair.

The execution-layer repair repeated the same acceptance on a newly created database-only project `pb-exec-repair-20261104` at port 61322: clean **33/33** migration replay, **19 pgTAP files / 508 assertions PASS**, including Deep budget concurrency 16/16, durability 23/23, infrastructure repair 24/24, LLM budget 15/15, and Web publication 33/33. DB lint returned zero errors and five existing warnings. The project was stopped with `--no-backup`; its container/volume were absent afterward, while the pre-existing 5432x and 5532x projects remained running.

### Initial REAL_PROVIDER verification — historical R2B/R2C failures

At execution time, the old deterministic fixtures produced reservations of $0.0420 for R2B and $0.0573 for R2C, including one possible bounded repair per operation. The combined $0.0993 stayed below the then-approved $0.10 cap. Because both old live outcomes lost authoritative usage, that full amount remains uncertain and is never reinterpreted as zero. The adapter now uses explicit-only caching with no breakpoints and rejects unexpected cache usage.

- R2B: OpenAI returned HTTP 400 before model output because `inferredAssumptions` used an untyped object map that was incompatible with strict Structured Outputs. No token usage was returned. The operation was conservatively settled as `outcome_unknown`, retaining the full $0.0420 reservation as uncertain. The schema now uses a typed `ProposedAssumption`; offline strict-schema validation passes. R2B was not retried.
- R2C: OpenAI returned a schema-shaped response, but Pydantic rejected `expectedInformationGain=10` and `dependencyImpact=10` because the contract maximum is 5. The old test process did not persist the `DeepModelValidationError.call` metadata before exiting, so token usage and actual cost cannot be reconstructed safely. The operation was conservatively settled as `outcome_unknown`, retaining $0.0573 as uncertain. Current official Structured Outputs documentation permits numeric `minimum`/`maximum` for this non-fine-tuned model; the Deep schema now emits both bounds, the prompt explicitly requires integer 1–5 scores, and Pydantic remains authoritative. R2C was not retried.
- Ledger before fixture cleanup: R2B `reserved 0.0420`, `uncertain 0.0420`; R2C `reserved 0.0573`, `uncertain 0.0573`; run/daily/monthly balances each `reserved 0`, `actual 0`, `uncertain 0.0993`.
- The live fixture was then removed precisely. The isolated database regression returned to **19 files, 507/507 assertions PASS**. No complete Deep Research workflow ran.

### Current official configuration and proposed new authorization

Official documentation checked on 2026-11-04 confirms `gpt-6.1-sol` supports Chat Completions, Structured Outputs, `low` reasoning effort, and 128K maximum output. `service_tier=default` is Standard. Standard prices are $2/M ordinary input, $0.10/M cached input, $2.50/M 30-minute cache writes, and $10/M output; reasoning tokens are included in output usage. Explicit cache mode without a breakpoint performs no cache write. Sources: [model](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create), [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), and [pricing](https://developers.openai.com/api/docs/pricing).

With 950 maximum completion tokens, low reasoning, Standard tier, explicit-only/no-breakpoint caching, and one possible repair per adapter, the deterministic fixtures calculated conservative maximum reservations of **R2B $0.0422** and **R2C $0.0565**, combined **$0.0987**. The user explicitly authorized this new maximum. The controlled R2B process used a stricter 945-token ceiling to keep its current fixture at $0.0422; the prepared evidence-bearing R2C fixture would use 775 tokens and estimates $0.0557 inside its approved $0.0565 reservation.

### Authorized REAL_PROVIDER verification — R2B FAIL, R2C BLOCKED

A new disposable Local PostgreSQL project `pb-r2bc-live-0rfldo` ran on port 59322. The original prior live fixture had already been removed; therefore this isolated record first re-established the old $0.0420 and $0.0573 unknown outcomes as two explicit carry-forward Ledger operations. Before the new call, run/daily/monthly each showed `uncertain=0.0993`. Available capacity was $0.3007 non-final run budget, $1.9007 daily, and $9.9007 monthly, so the approved $0.0987 fit every limit without consuming the protected $0.10 final reserve.

Exactly one new R2B request was made. The $0.0422 reservation was durable and `running` before the provider boundary. No automatic repair or retry was enabled. OpenAI returned a strict-schema-shaped proposal, but conversion into the authoritative `ResearchPlanV1` failed because the model emitted `evidenceType="Internal specifications and acceptance criteria"`; `EvidenceRequirementV1` permits only `fact`, `comparison`, `trend`, `counter_evidence`, `primary_source`, or `other`. Root cause: `ProposedRequirement.evidenceType` was typed as unconstrained `str`; commit `ed2a48e` replaces it with the authoritative shared enum and adds the observed-value regression.

- Provider/model/tier: `openai` / `gpt-6.1-sol` / `default`.
- Request ID: `req_41b66c2c5a6447e4b05bb5ce32e0bac6`.
- Usage: 878 input, 834 output, 1,712 total, 0 cached input, 0 cache-write, 0 reasoning tokens.
- Priced cost: `(878 × $2 + 834 × $10) / 1M = $0.010096`, conservatively settled as **$0.0101**.
- Ledger: operation `failed`; $0.0101 `settled`, $0.0321 `released`; run/daily/monthly each ended with `reserved=0`, `actual=0.0101`, `uncertain=0.0993`.
- ResearchPlan: **not produced** because authoritative contract validation failed.
- R2C: **not reserved and not called**. The database confirmed the R2C operation did not exist, per the stop-on-first-failure rule.

The disposable stack was stopped with `--no-backup`; its container and volume were confirmed absent. Existing local projects on ports 5432x and 5532x remained running and untouched. No complete Deep Research workflow ran.

## Repair commit

- `e606109` — `fix(research): harden real provider verification`
- `ed2a48e` — `fix(research): align deep provider schemas`
- `87d14a3` — `fix(research): harden deep execution boundaries`

## Remaining blockers

1. R2B/R2C execution-layer safety is prepared for another separately authorized real-provider verification, but this repair intentionally made no paid call. Candidate ceilings are not yet proven sufficient by a real response.
2. R2A/R2E/R2F paid-call ledger integration remains outside this change and blocks unrestricted full paid Deep E2E. Do not run a complete paid Deep workflow until that is implemented.
3. R2C remains real-provider unverified. Web provider and frontend work were intentionally out of scope.
4. A durable reconciliation procedure is still required for a crash after a provider response but before its output/cost metadata becomes durable; the coordinator now blocks the ambiguous `running` operation instead of replaying it.

## R2B validation diagnostics and gate alignment (2026-11-04)

The latest authorized R2B verification consumed one initial call and one bounded repair. Both returned `finish_reason=stop`; aggregate usage was 2,115 input, 1,369 output, 0 reasoning, and the durable Ledger settled $0.0181 while releasing $0.0578. Both attempts were rejected before an authoritative ResearchPlan was produced, so R2C was not reserved or called. The old process retained request IDs and usage but not the validation stage or rule. The raw outputs are unavailable; the exact historical rejection reasons remain **UNKNOWN** and are not inferred from later fixtures.

Migration `20261104054237_deep_research_validation_diagnostics.sql` adds append-only `research_validation_diagnostics`. Each row is fenced to a running Operation, Job lease owner, and lease generation and stores only attempt/stage, request ID, finish reason, token counts, validation stage, error code, field path, rule ID, and a bounded printable-ASCII summary. It deliberately has no Prompt, Evidence, API key, or raw model-output field. Authenticated owners have RLS-scoped read access; cross-user reads and all authenticated writes are denied. Worker writes use `record_deep_research_validation_diagnostic(...)`; stale, terminal, and conflicting replay writes fail closed.

Owner query:

```sql
select attempt_number, attempt_stage, provider_request_id, finish_reason,
       input_tokens, output_tokens, reasoning_tokens, validation_stage,
       error_code, field_path, rule_id, diagnostic_summary, created_at
from public.research_validation_diagnostics
where research_run_id = :run_id and operation_id = :operation_id
order by attempt_number;
```

Tracked examples now covered offline and in pgTAP:

- Initial DTO failure: `provider_dto / provider_dto_invalid`, exact Pydantic field path and rule, with authoritative request/usage metadata.
- Repair quality rejection: `quality_gate / quality_gate_rejected`, for example `essentialClarifications / no_unresolved_essential_clarifications`.
- Exhausted bounded repair: a separate attempt 3 `fallback / r2b_fallback_used / bounded_repair_exhausted` row. A successful repair instead records attempt 2 as `accepted` and creates no fallback row.

Confirmed Prompt/DTO/Contract/gate mismatches and fixes:

1. The DTO permits up to four queries per subquestion while the request has a dynamic total `maxQueries`; the Prompt did not explicitly state the aggregate limit. The Prompt now does, and backend rejection reports `retrievalProposals / effective_query_limit` without truncation.
2. The authoritative contract requires the objective to remain exact while the Prompt only said to preserve it. The Prompt now says to copy it exactly; the backend rule remains authoritative.
3. The DTO permits essential clarifications while the gate rejects any unresolved clarification. The Prompt now says to keep the list empty when planning is possible and to express evidence gaps as requirements. The safety gate remains unchanged.
4. Duplicate subquestion keys could overwrite conversion mappings and unknown dependency keys were silently discarded. DTO validation now rejects both before conversion.
5. The comparison gate used model-authored `normalizedResearchIntent`, which could falsely introduce comparison semantics. It now evaluates the already-authoritative plan objective.
6. Repair previously received a generic exception string and second-gate failure lost its rule. Repair now receives one safe structured `stage/code/field/rule/summary` diagnostic and remains capped at one attempt.

Verification used disposable project `pb-r2b-diag-20261104` on port 63322. Clean replay applied **34/34 migrations**. Complete pgTAP: **20 files / 540 assertions PASS**; diagnostics protocol **32/32 PASS**; focused existing durability, infrastructure repair, LLM budget, and two-session concurrency **78/78 PASS**. Public-schema lint reported zero errors. Worker offline suite: **215 passed and 9 subtests passed**; focused R2B/R2C/coordinator/contracts suite: **67/67 PASS**. No paid call or hosted database access occurred.

The next real test must use a new explicit authorization and fresh atomic reservation. Acceptance requires initial or repair diagnostic `validationStage=accepted`, no fallback row, a persisted authoritative ResearchPlan, complete request/finish/usage metadata, and exact Ledger settlement/release. The historical raw output and its exact field/rule failures remain UNKNOWN.

## R2C durable diagnostics and research-quality semantics (2026-11-04)

Migration `20261104065453_extend_validation_diagnostics_to_r2c.sql` extends the existing append-only diagnostics table without replacing the R2B protocol. Each row now identifies `research_module` (`r2b` or `r2c`) and records bounded per-attempt latency and priced cost in addition to request ID, finish reason, token usage, validation stage, error code, field path, rule ID, and safe summary. Old `r2b-validation-diagnostic-v1` payloads remain valid; R2C uses `r2c-validation-diagnostic-v1` through the lease-fenced service-role RPC `record_deep_research_evaluation_diagnostic(...)`. Existing forced owner RLS, authenticated read-only access, cross-user denial, append-only trigger, replay conflict checks, and operation lease generation fencing remain in force.

Owner query:

```sql
select research_module, attempt_number, attempt_stage, provider_request_id,
       finish_reason, input_tokens, output_tokens, reasoning_tokens,
       attempt_cost_usd, latency_ms, validation_stage, error_code,
       field_path, rule_id, diagnostic_summary, created_at
from public.research_validation_diagnostics
where user_id = auth.uid() and research_run_id = :run_id
order by operation_id, attempt_number;
```

R2C now durably distinguishes accepted output, Provider DTO rejection, backend-contract rejection, Provider terminal state, bounded-repair outcome, and fallback. Initial and repair attempts retain their own authoritative metadata. No Prompt, raw Evidence, API key, or raw model output is stored. The operation Ledger remains the authoritative source for reservation and settlement; the diagnostic cost is an immutable per-attempt observation and does not mutate balances.

The Contract, Prompt, and R2D decision rule now share one definition: `overallStatus=sufficient` means the available evidence is adequate to answer the research question, not that a proposed claim is true. A well-supported negative answer may therefore be sufficient. `sufficient` additionally requires every evidence requirement to be `satisfied`, every sufficiency decision to be `pass`, and no critical gap. An unresolved material contradiction remains `conflicted`; differences caused by condition, scope, date, or definition are represented as such and do not by themselves force another research round. R2D reaches the quality target only when this sufficiency condition holds without a material contradiction or critical gap.

Offline quality fixtures cover: sufficient supporting evidence, sufficient refuting evidence, insufficient evidence, material contradiction, condition-dependent outcomes, and non-independent sources. R2C-to-R2D contract tests verify that evidence sufficiency, claim support, contradictions, and critical gaps cannot be conflated.

Verification used a newly created disposable Local Supabase/PostgreSQL stack on database port 65422. It replayed **35/35 migrations**, then ran six focused pgTAP files with **138/138 assertions PASS**, including the new R2C diagnostics protocol **28/28**, existing R2B compatibility, owner/cross-user RLS, stale-lease rejection, append-only enforcement, durability, infrastructure repair, Ledger accounting, and two-session concurrency. The stack was stopped after verification. Worker results: focused R2B/R2C/R2D **75/75 PASS**, complete Research offline **100/100 PASS**, and complete non-provider Worker regression **232 passed plus 9 subtests**. The three explicitly live-provider test modules were not executed because this task prohibited paid calls.

No paid Provider call, hosted Supabase access, Legacy/R1/R2A/R2E/R2F change, push, merge, or deployment occurred. Remaining full-Deep blocker: paid R2A/R2E/R2F Ledger/checkpoint integration is still outside this change; real-provider R2C diagnostics have not been re-exercised after this additive persistence change.
