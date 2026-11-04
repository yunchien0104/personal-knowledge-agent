# Deep Research R2B/R2C LLM Integration

Date: 2026-11-04

Branch: `feat/deep-research-llm-integration`

Baseline: `00d3c15`

## Result

Status: **PARTIAL — implementation, offline verification, and Local DB verification pass; REAL_PROVIDER is BLOCKED.**

R2B and R2C are wired into the Deep Worker path through production OpenAI-compatible adapters. Legacy Planner/R1/R2A/R2E/R2F construction and behavior were not changed. No paid call, hosted Supabase access, deployment, push, or merge occurred.

Real-provider verification was deliberately not attempted because `worker/.env` does not contain explicit `R2B_MODEL`, `R2C_MODEL`, `R2B_PRICING_VERIFIED=true`, `R2C_PRICING_VERIFIED=true`, or the four per-million-token price values. An API key and a general research model are present, but substituting or guessing either model or pricing would violate the fail-closed verification requirements. Provider cost: **$0.0000**.

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

## Verification

### MOCK — PASS

```sh
PYTHONPATH=worker/src:. worker/.venv/bin/pytest worker/tests -q \
  --ignore=worker/tests/research/test_atomic_generator.py \
  --ignore=worker/tests/research/test_claim_verifier.py \
  --ignore=worker/tests/research/test_research_generator.py \
  --ignore=worker/tests/research/test_research_pipeline_live.py
```

Result: **178 passed, 9 subtests passed**. The four excluded modules execute provider paths during collection or as script entry points; no paid/provider operation was permitted. Coverage includes metadata compatibility, strict schemas, bounded repair, missing-usage unknown outcomes, pricing, reserve-before-call order, actual settlement, fallback/error truthfulness, post-call deadline fencing, Worker injection, and Legacy separation.

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

### REAL_PROVIDER — BLOCKED

No request was sent and no provider output exists. Required explicit B/C model and verified pricing configuration is absent. This prevented establishing safe worst-case reservations under the approved $0.10 initial B/C test cap. The configured key was never printed or used.

## Remaining blockers

1. Add explicit approved `R2B_MODEL` and `R2C_MODEL` values and independently verified price values/flags, then run exactly one non-sensitive bounded R2B call and one bounded R2C call with total reservations at or below $0.10. Verify response schema, authoritative usage, request IDs, actual ledger settlement, and no fallback.
2. R2A/R2E/R2F paid-call ledger integration remains outside this change and blocks unrestricted full paid Deep E2E. Do not run a complete paid Deep workflow until that is implemented.
3. Provider/staging credentials and adapter behavior remain unverified; Web provider and frontend work were intentionally out of scope.
