# Deep Research R2B/R2C LLM Integration

Date: 2026-11-04

Branch: `feat/deep-research-llm-integration`

Baseline: `00d3c15`

## Result

Status: **PARTIAL — implementation, offline verification, and Local DB verification pass; the first bounded REAL_PROVIDER verification exposed strict-schema defects and remains BLOCKED.**

R2B and R2C are wired into the Deep Worker path through production OpenAI-compatible adapters. Legacy Planner/R1/R2A/R2E/R2F construction and behavior were not changed. No paid call, hosted Supabase access, deployment, push, or merge occurred.

The initial missing model and pricing configuration was supplied on 2026-11-04. The bounded live verification below used `gpt-6.1-sol` with Standard service tier, low reasoning effort, and explicit $2/M input and $10/M output pricing. The configured API key was neither printed nor overwritten.

## Implementation

- `worker/src/personal_brain/llm_client.py`: additive `generate_with_metadata()` returning validated structured output, provider/model, authoritative usage, latency, and request ID. Existing `generate()` delegates to it and preserves its return contract.
- `worker/src/personal_brain/research/deep_llm.py`: separately configured R2B and R2C adapters, provider-compatible strict schemas, Pydantic validation, one bounded repair, verified pricing, conservative input/output upper-bound reservations, and explicit unknown-outcome errors.
- `worker/src/personal_brain/research/deep_planning.py`: aggregates initial/repair usage and cost; marks fallback distinctly and propagates provider failures.
- `worker/src/personal_brain/research/deep_evaluation.py`: model output excludes backend-owned identity and telemetry fields; the backend adds them after validation. Success and fallback are persisted distinctly.
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

Latest result: **183 passed, 9 subtests passed**. The four excluded modules execute provider paths during collection or as script entry points. Coverage includes metadata compatibility, detailed reasoning/cache usage, Standard-tier, explicit cache and reasoning parameters, fail-closed unexpected cache writes, strict schemas, bounded repair, missing-usage unknown outcomes, pricing, reserve-before-call order, settlement, fallback/error truthfulness, post-call deadline fencing, Worker injection, and Legacy separation.

### LOCAL_DB — PASS

An isolated disposable project `personal-brain-deep-llm-20261104` used PostgreSQL 15.19 on port 58322 with a unique Docker network and volume. Existing local projects on ports 5432x and 5532x were inventoried and untouched. Only the isolated database service was started. It was stopped with `--no-backup`, and its container and volume were removed after verification.

```sh
supabase start --workdir work/deep-llm-db.UO3lv4 \
  -x gotrue,realtime,storage-api,imgproxy,kong,mailpit,postgrest,postgres-meta,studio,edge-runtime,logflare,vector,supavisor
supabase migration list --local --workdir work/deep-llm-db.UO3lv4
supabase test db --workdir work/deep-llm-db.UO3lv4
supabase db lint --local --schema public --level warning --fail-on error \
  --workdir work/deep-llm-db.UO3lv4
```

Results:

- Clean migration replay: **33/33 migrations**.
- pgTAP: **19 files, 507/507 assertions PASS**.
- Dedicated R2B/R2C real PostgreSQL budget test: **14/14 PASS**, including separate reservations, starts, settlements, releases, ledger events, exact run/daily/monthly balances, and hard daily/monthly caps.
- DB lint: **0 errors**; five pre-existing warnings across four functions.

### REAL_PROVIDER — BLOCKED after one R2B and one R2C request

At execution time, the deterministic fixtures produced reservations of $0.0420 for R2B and $0.0573 for R2C, including one possible bounded repair per operation. The combined $0.0993 stayed below the approved $0.10 cap under the supplied $2/M input and $10/M output rates. Subsequent official-document review found that GPT-6.1 Sol implicit 30-minute cache writes have a distinct $2.50/M rate. Because both live responses lost authoritative usage, their true cache-write component is unknown; the recorded $0.0993 must not be presented as actual spend. The adapter now disables implicit caching and rejects unexpected cache usage. After the strict-schema fixes and a 950-token output limit, the current worst-case reservations are R2B $0.0423 plus R2C $0.0563 = **$0.0986**.

- R2B: OpenAI returned HTTP 400 before model output because `inferredAssumptions` used an untyped object map that was incompatible with strict Structured Outputs. No token usage was returned. The operation was conservatively settled as `outcome_unknown`, retaining the full $0.0420 reservation as uncertain. The schema now uses a typed `ProposedAssumption`; offline strict-schema validation passes. R2B was not retried.
- R2C: OpenAI returned a schema-shaped response, but Pydantic rejected `expectedInformationGain=10` and `dependencyImpact=10` because the contract maximum is 5. The provider-compatible schema had removed unsupported JSON Schema numeric bounds. The test process did not persist the `DeepModelValidationError.call` metadata before exiting, so token usage and actual cost cannot be reconstructed safely. The operation was conservatively settled as `outcome_unknown`, retaining $0.0573 as uncertain. Numeric bounds are now preserved as schema descriptions while Pydantic remains authoritative. R2C was not retried.
- Ledger before fixture cleanup: R2B `reserved 0.0420`, `uncertain 0.0420`; R2C `reserved 0.0573`, `uncertain 0.0573`; run/daily/monthly balances each `reserved 0`, `actual 0`, `uncertain 0.0993`.
- The live fixture was then removed precisely. The isolated database regression returned to **19 files, 507/507 assertions PASS**. No complete Deep Research workflow ran.

## Remaining blockers

1. Do not retry under the same $0.10 test budget: its full $0.0993 is conservatively accounted as uncertain. A future explicitly approved test budget must catch and durably record validation-error call metadata before process exit, then verify the repaired strict schemas with fresh reservations.
2. R2A/R2E/R2F paid-call ledger integration remains outside this change and blocks unrestricted full paid Deep E2E. Do not run a complete paid Deep workflow until that is implemented.
3. Provider/staging credentials and adapter behavior remain unverified; Web provider and frontend work were intentionally out of scope.
