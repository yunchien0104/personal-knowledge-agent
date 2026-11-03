# Deep Research Infrastructure Repair

Date: 2026-11-04
Branch: `fix/deep-research-infrastructure`
Baseline: `7c0a4ea`

## 1. Original findings and root causes

1. Migration history was not reproducible because versions `20261030090000` and
   `20261030090100` were missing from this worktree. Their exact original files
   were recoverable from local Git history and matched copies in the related local
   repositories byte-for-byte.
2. `reserved` operations were owned by a lease generation forever. A new Worker
   could not distinguish a committed reservation that had never started from a
   side effect that might already have happened.
3. Settlement selected the current calendar month instead of the reservation's
   month and collapsed release accounting into a single event.
4. `maxDurationSeconds` was request metadata only. There was no durable deadline,
   restart-safe hard stop, or database boundary preventing a late operation.
5. A non-published qualified partial could be represented as `draft_ready`, and
   successful Deep publication had no terminal replay result after a lost response.
6. The authenticated GET path returned Legacy citations only and omitted persisted
   Deep result/Web provenance and current source validity.

## 2. Repairs and state transitions

### Phase 1 — migration integrity

- Restored authentic migrations `20261030090000_auto_social_source_notes.sql` and
  `20261030090100_add_job_workflow_progress.sql` without editing their contents.
- Confirmed their local history origins (`d234228` and `fd6a805`) and identical
  hashes across the available repositories.
- Clean replay remains blocked because this environment cannot create or reach a
  disposable Supabase Local database.

### Phases 2–3 — operations and budget

- Added `reservation_period_start` and used it for every later budget mutation.
- `reserved + stale lease` now transfers ownership once with no budget/ledger
  duplication. `running + stale lease + paid cost` becomes `outcome_unknown` and
  is never automatically re-executed. Zero-cost internal work can return to
  `reserved` under the new lease.
- Settlement rejects actual cost above the atomic reservation, records actual and
  unused release separately, and returns stable terminal results on replay.
- Added deterministic pgTAP fixtures and a real two-connection `dblink` contention
  test. The latter holds the first transaction open while the second reservation
  waits, then proves only one 0.3000 reservation fits behind the protected 0.1000
  final reserve of a 0.5000 run.

### Phase 4 — limits, terminal state, and publication replay

- Added a stable `deadline_at` for Deep runs and an insert trigger for new runs.
- Database triggers reject operation creation after the deadline and enforce the
  configured query count. The start RPC also rejects a late start.
- Coordinator checks the persisted deadline before planning, on every loop, after
  retrieval, and after restart. It does not begin synthesis or publication after
  duration exhaustion.
- Added `partial_ready`, `insufficient`, and `timed_out`. Only an actual persisted
  published draft is `draft_ready`.
- Wrapped the existing verified publication RPC with an exact terminal replay
  check. Existing R2A/R2E/R2F code and gates were not changed.

### Phase 5 — Web evidence and API integrity

- Deep API reads now include verified/insufficient result metadata and Web citation
  snapshots, then join current Web Evidence only to determine validity.
- Ineligible, missing, superseded, and deleted evidence is mapped to a non-valid
  citation status. Internal asset signing ignores nullable Web asset IDs.
- Legacy reads intentionally avoid all Deep-only tables.
- Added cross-owner RLS assertions and publication execute-privilege rejection.
- Real source revalidation remains a future provider responsibility: it must call
  the existing lifecycle RPC after an allowlisted fetch. No Web networking was
  implemented in this task.

## 3. Files and migrations

New migrations:

- `supabase/migrations/20261103191702_repair_deep_research_durability.sql`
- `supabase/migrations/20261103191755_repair_deep_research_state.sql`

New tests:

- `supabase/tests/deep_research_infra_repair.test.sql`
- `supabase/tests/deep_research_budget_concurrency.test.sql`

Modified areas:

- Deep coordinator/contracts and coordinator tests under `worker/`.
- Research read mapping, route signing, shared UI types, and tests under `web/`.
- Existing Web publication pgTAP test for replay and cross-owner authorization.
- Restored historical migration files listed in Phase 1.

## 4. Test evidence

| Command | Resource | Result | Demonstrates |
| --- | --- | --- | --- |
| `pytest tests -q` with four explicit offline exclusions and loopback test env | deterministic Worker fakes | 157 passed | Legacy/Deep regressions, coordinator recovery, hard stops |
| `pytest tests/test_http_server.py -q` | loopback HTTP server | 8 setup failures: sandbox denied socket bind | Environment limitation; assertions did not run |
| Seven offline `npm run test*` commands | Node fakes/fixtures | 34 passed | Web contracts and Legacy/Deep read compatibility |
| `npm run typecheck` | local TypeScript | passed | Type correctness |
| `npm run lint` | local ESLint | passed | Static lint |
| `npm run build` | Next webpack | blocked on DNS for Google Fonts | Compile could not complete in offline sandbox |
| `supabase status -o json` / local DB checks | Supabase Local | blocked on Docker socket/no local endpoint | No database was touched |
| pgTAP and `dblink` concurrency files | real PostgreSQL required | authored, not run | No DB acceptance claim |

All environment URLs used for offline commands were overridden with loopback test
values. No secret value was printed or changed.

## 5. Resolved versus blocked

Resolved in code:

- Authentic migration baseline files.
- Pre-start reservation recovery and stale-worker fencing contract.
- Original-period, idempotent budget settlement and explicit release ledger.
- Durable deadline/query boundary and truthful partial/timeout state.
- Exact publication replay without duplicate Notes/citations.
- Authenticated Deep result/Web provenance read mapping with Legacy isolation.

Blocked pending isolated PostgreSQL:

- Clean migration replay and SQL runtime validation.
- pgTAP/RLS/RPC execution.
- Real two-session concurrency acceptance.
- Migration lint against the complete schema.

Blocked/out of scope pending later provider work:

- Provider-reported costs at every R2B/R2C/R2A/R2E/R2F call site.
- Actual Web source revalidation, redirect/DNS/IP enforcement, and live Web calls.
- Real model quality, token/cost/latency, and Staging E2E.

## 6. Compatibility impact

- Existing applied migrations were not rewritten; repairs are additive.
- Legacy request routing, Legacy database RPCs, R1 retrieval, R2A synthesis, R2E
  verification, R2F citation gate, prompts, model configuration, and provider
  adapters were not modified.
- New Deep statuses require consumers to treat status as an open string union; the
  current Web types already do so.

## 7. Prerequisites for Step 2 real LLM integration

1. Pass clean migration replay, all relevant pgTAP, and the two-session test on a
   disposable database.
2. Apply only to an explicitly designated isolated Staging project and rerun RLS,
   RPC, stale-lease, publication replay, and API E2E checks.
3. Define a provider-cost adapter that reserves before each paid boundary and
   settles from the provider's authoritative usage response.
4. Establish hard per-run/test spend caps and redacted telemetry.
5. Keep real Web revalidation and networking in a separately reviewed provider
   adapter with DNS, redirect, response-size, content-type, and SSRF controls.

The repair is not a claim that Deep Research or provider integration is complete.
