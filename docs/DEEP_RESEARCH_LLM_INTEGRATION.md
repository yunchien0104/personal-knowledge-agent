# Deep Research R2B/R2C LLM Integration

Date: 2026-11-04

Branch: `feat/deep-research-llm-integration`

Baseline: `00d3c15`

## Result

Status: **PARTIAL — schema/error handling and Local DB budget verification pass; the authorized repaired R2B call exposed a second contract defect, so R2C was not run.**

R2B and R2C are wired into the Deep Worker path through production OpenAI-compatible adapters. Legacy Planner/R1/R2A/R2E/R2F construction and behavior were not changed. The explicitly authorized verification below made one bounded R2B paid call. It did not call R2C, access hosted Supabase, deploy, push, or merge.

The initial missing model and pricing configuration was supplied on 2026-11-04. The bounded live verification below used `gpt-6.1-sol` with Standard service tier, low reasoning effort, and explicit $2/M input and $10/M output pricing. The configured API key was neither printed nor overwritten.

## Implementation

- `worker/src/personal_brain/llm_client.py`: additive `generate_with_metadata()` returning validated structured output, provider/model, authoritative usage, latency, and request ID. Existing `generate()` delegates to it and preserves its return contract. Provider rejection, timeout, transport failure, and invalid structured response now have distinct error kinds while retaining any available usage/request metadata.
- `worker/src/personal_brain/research/deep_llm.py`: separately configured R2B and R2C adapters, provider-compatible strict schemas, Pydantic validation, one bounded repair, verified pricing, conservative input/output upper-bound reservations, and explicit unknown-outcome errors. Unexpected service tier, cache use, or missing usage fail closed because configured Standard pricing would no longer be authoritative.
- `worker/src/personal_brain/research/deep_planning.py`: aggregates initial/repair usage and cost; marks fallback distinctly and propagates provider failures.
- `worker/src/personal_brain/research/deep_evaluation.py`: model output excludes backend-owned identity and telemetry fields; the backend adds them after validation. Success and fallback are persisted distinctly.
- `worker/src/personal_brain/research/schema_utils.py`: Deep schemas preserve provider-supported numeric bounds while the default mode retains Legacy compatibility. R2C scores remain independently enforced by Pydantic and are never coerced.
- `worker/src/personal_brain/research/deep_coordinator.py`: reserves before every R2B/R2C operation, settles actual priced usage, preserves the entire reservation as uncertain when the provider outcome/usage is unknown, records request IDs, and rechecks lease/deadline after calls before persistence.
- `worker/src/personal_brain/runner.py`: injects both production adapters only into the Deep coordinator. Missing model/pricing configuration fails closed. Legacy injection is unchanged.
- `supabase/migrations/20261104002931_enforce_deep_research_daily_budget.sql`: additive daily aggregate budget with Asia/Taipei date boundary; atomic run/daily/monthly reservation and settlement locks. Limits remain $0.50/run, $2/day, $10/month, with $0.10 protected final-stage reserve.
- `.env.example` and `render.yaml`: server-only configuration names; no secret values.

## Configuration

Required shared values: `R2B_R2C_API_BASE_URL`, `R2B_R2C_API_KEY`, `R2B_R2C_PROVIDER`.

Required per adapter: `R2B_MODEL` / `R2C_MODEL`, `R2B_MAX_OUTPUT_TOKENS` / `R2C_MAX_OUTPUT_TOKENS`, `R2B_TIMEOUT_SECONDS` / `R2C_TIMEOUT_SECONDS`, `{R2B|R2C}_PRICING_VERIFIED=true`, `{R2B|R2C}_INPUT_COST_PER_MILLION_USD`, and `{R2B|R2C}_OUTPUT_COST_PER_MILLION_USD`.

Reasoning-model configuration also supports `R2B_R2C_SERVICE_TIER`, `R2B_R2C_PROMPT_CACHE_MODE`, and `{R2B|R2C}_REASONING_EFFORT`. Deep calls now use `default` (Standard), `low`, `explicit` prompt-cache mode with no breakpoints, and a 90-second timeout. Disabling implicit cache writes keeps the verified $2/M ordinary-input price authoritative; any unexpected cache usage fails closed as `outcome_unknown`. The current bounded test setting is 950 maximum completion tokens per adapter.

## Verification

### MOCK — PASS

```sh
PYTHONPATH=worker/src:. worker/.venv/bin/pytest worker/tests -q \
  --ignore=worker/tests/research/test_atomic_generator.py \
  --ignore=worker/tests/research/test_claim_verifier.py \
  --ignore=worker/tests/research/test_research_generator.py \
  --ignore=worker/tests/research/test_research_pipeline_live.py
```

Latest result: **189 passed, 9 subtests passed**. The four excluded modules execute provider paths during collection or as script entry points. Coverage includes metadata compatibility, detailed reasoning/cache usage, Standard-tier, explicit cache and reasoning parameters, fail-closed unexpected cache writes/service tiers, strict schemas, bounded repair diagnostics, missing-usage unknown outcomes, pricing, reserve-before-call order, settlement, fallback/error truthfulness, post-call deadline fencing, Worker injection, and Legacy separation. Focused regressions reproduce the prior untyped R2B object and out-of-range R2C scores; the latter proves the invalid first response is not coerced and its usage/request ID/cost survive the single bounded repair.

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

Exactly one new R2B request was made. The $0.0422 reservation was durable and `running` before the provider boundary. No automatic repair or retry was enabled. OpenAI returned a strict-schema-shaped proposal, but conversion into the authoritative `ResearchPlanV1` failed because the model emitted `evidenceType="Internal specifications and acceptance criteria"`; `EvidenceRequirementV1` permits only `fact`, `comparison`, `trend`, `counter_evidence`, `primary_source`, or `other`. Root cause: `ProposedRequirement.evidenceType` is still typed as unconstrained `str`, so the provider schema did not enforce the backend enum.

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

## Remaining blockers

1. Change `ProposedRequirement.evidenceType` to the same closed enum used by `EvidenceRequirementV1`, add a regression for the observed value, and re-run offline tests. Do not retry R2B or run R2C without another explicit paid-test authorization.
2. R2A/R2E/R2F paid-call ledger integration remains outside this change and blocks unrestricted full paid Deep E2E. Do not run a complete paid Deep workflow until that is implemented.
3. R2C remains real-provider unverified. Web provider and frontend work were intentionally out of scope.
