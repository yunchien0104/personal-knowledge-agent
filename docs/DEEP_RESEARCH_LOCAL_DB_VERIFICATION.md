# Deep Research Local Database Verification

Date: 2026-11-04
Branch: `fix/deep-research-infrastructure`
Baseline HEAD: `0ef5fdb`
Verification fix: `c775e44`

## Environment and isolation

- Supabase CLI: `2.121.0`.
- OrbStack Docker Engine: `29.8.2`.
- Final disposable database: PostgreSQL `15.19`.
- Existing stacks were inventoried before testing:
  - `personal-brain` on ports `5432x`, with its own DB volume.
  - `personal-brain-notes-dev` on ports `5532x`, with its own DB volume.
- The clean verification stack used project ID
  `personal-brain-deep-step3-final-20261104`, database port `57322`, network
  `supabase_network_personal-brain-deep-step3-final-20261104`, and volume
  `supabase_db_personal-brain-deep-step3-final-20261104`.
- Its workdir was an isolated copy under the Codex `work/deep-db-step3-final`
  directory. Repository config and the two pre-existing stacks were not changed.
- Only the PostgreSQL service was started. No hosted Supabase endpoint was used.
- After verification, both Step 3 disposable stacks were stopped with explicit
  project IDs and `--no-backup`; their containers and volumes were confirmed absent.
  The two pre-existing local stacks remained healthy and running.

Classification: **PASS** — container, port, network, volume, and project labels
proved that the database was newly created and isolated.

## Migration replay

- The restored historical migrations have identical SHA-256 hashes to the
  authenticated copies in `/Users/yunchien/programming/knowledgeagent`:
  - `20261030090000`: `f70776d8960e45a0feb1921029a4d5048292d9e32dc7483f8bbe1ed8fd951c68`
  - `20261030090100`: `a607f91fef10c46963cf7d2271d72a1f7ee5cd025a1b8c9e654eecd78655c359`
- A newly created empty volume applied all 32 migrations in timestamp order.
- `supabase migration list --local` reported all 32 local versions present in the
  database history, including the final follow-up migration
  `20261103195628_fix_deep_partial_completion_lookup.sql`.
- Resulting schema: 36 public tables, all 36 public tables with RLS and forced RLS,
  47 public policies, and 18 Deep-related functions before the follow-up audit.

Classification: **PASS** — clean replay was performed twice during diagnosis; the
second replay contained the final migration and required no manual schema repair.

## pgTAP, RLS, and RPC verification

Command:

```text
supabase test db --workdir <isolated-final-workdir> --local
```

Final result:

```text
Files=18, Tests=490
All tests successful.
Result: PASS
```

Deep-specific real PostgreSQL results:

- `deep_research_budget_concurrency.test.sql`: 13/13 PASS.
- `deep_research_durability.test.sql`: 23/23 PASS.
- `deep_research_infra_repair.test.sql`: 24/24 PASS.
- `deep_research_web_publication.test.sql`: 33/33 PASS.
- Total Deep-specific assertions: 93/93 PASS.

The full 490 assertions also covered Legacy Research, queue, Storage isolation,
source notes, retrieval, social ingestion, publication, and other existing schema
protocols. This verifies compatibility rather than changing those workflows.

Verified authorization properties include:

- All audited Deep tables have RLS and forced RLS.
- Cross-owner Deep result and Web citation reads return zero rows.
- `authenticated` cannot execute Worker publication or mutation RPCs.
- `service_role` retains the required fenced RPC privileges.
- Stale lease generations cannot reserve, start, checkpoint, settle, or publish.
- Publication replay cannot create duplicate Notes or citations.

Classification: **PASS**.

## Real two-session concurrency

The test used two independent `dblink` PostgreSQL backend PIDs. Connections use
the database container's bridge address and internal server port, so they cannot
accidentally target another host-published Supabase stack.

Verified sequence:

1. Session one reserved `0.3000` of a `0.5000` run while its transaction remained
   uncommitted.
2. Session two overlapped and blocked on the locked budget row.
3. After session one committed, session two returned `budget_exhausted` because the
   protected final reserve was `0.1000`.
4. Exactly one operation and one reservation ledger event existed.
5. Two sessions then settled the winning operation concurrently at `0.2000`.
6. The first settlement mutated accounting; the second returned an idempotent replay.
7. Final run balance was `reserved=0`, `actual=0.2000`, `uncertain=0`.
8. Ledger contained exactly three events: reserve, settle, and release; duplicate
   settlement added no row.

The real-database repair fixture separately verified abandoned pre-start reservation
takeover, no double reservation, stale Worker rejection, exact `0.0200` release on a
`0.0500` reservation settled at `0.0300`, and idempotent settlement replay.

Classification: **PASS** — these were actual simultaneous PostgreSQL sessions, not
threads sharing one connection or mocks.

## Crash/resume and persistent deadline

- Worker A committed checkpoint version 1 and a durable run deadline.
- Worker B, under lease generation 2, loaded checkpoint version 1 and the identical
  deadline; stale Worker A could not commit checkpoint version 2.
- A separate committed fixture was persisted, the disposable PostgreSQL container
  was stopped while retaining only its dedicated volume, and the same stack was
  started from backup.
- After this real database restart, the expired lease was reclaimed as generation 2,
  `load_deep_research_state` returned checkpoint 1, and `deadlineAt` exactly matched
  the persisted `research_runs.deadline_at` value.
- `outcome_unknown` retained the full uncertain amount and was not re-executed.
- All restart fixtures were deleted exactly; zero `@example.test` users remained.

Classification: **PASS**.

## Publication and terminal-state verification

- A verified partial persisted without creating a Note or output Note ID.
- The final run status remained `partial_ready`, not `draft_ready` or `failed`.
- Deleted/superseded Web Evidence could not pass publication validation.
- Successful publication followed by a simulated lost response returned the same
  Note on replay.
- Note count, Web citation count, and result count remained one.
- A different authenticated user could neither read the result/citations nor invoke
  publication replay.

Classification: **PASS**.

## Defects discovered and fixed

### 1. Generic Job trigger overwrote truthful Deep terminal state

Initial real result:

```text
deep_research_infra_repair.test.sql: 19/20 passed
have: failed
want: partial_ready
```

Root cause: `sync_research_run_job_status()` treated every completed Research Job
without a Note as failed. The Deep completion RPC correctly calculated and returned
`partial_ready`, but the subsequent Job update trigger overwrote it.

Fix: additive migration `20261103195628_fix_deep_partial_completion_lookup.sql`
preserves an already-derived Deep terminal state while retaining the original
Legacy mapping unchanged.

### 2. Concurrency tests could target the wrong local stack

Initial result: both dblink files stopped before assertions because loopback trust
authentication is rejected for a non-superuser dblink connection. Their original
hard-coded host port `54322` was also unsafe with multiple local stacks.

Fix: both tests now derive `inet_server_addr()` and `inet_server_port()` and connect
through the database container bridge with SCRAM. This is isolated and independent
of the published host port.

### 3. Append-only ledger blocked test-fixture cascade cleanup

The concurrency assertions passed, but fixture deletion correctly triggered the
append-only ledger guard. Cleanup now disables only that named trigger inside one
owner transaction, deletes the exact fixed test user, re-enables the trigger, and
rolls back all trigger-state changes if cleanup fails.

All affected tests and the complete suite passed after these fixes.

## Lint and non-database regressions

- `supabase db lint --local --schema public --level warning --fail-on error`:
  PASS with zero errors and five warnings across four functions.
- Warnings: one pre-existing unused Instagram variable, two composite OUT-variable
  diagnostics in `deep_research_job_context`, and two unused row-variable diagnostics
  in append functions. They do not represent failed typing or authorization checks.
- Worker offline regression: 157/157 PASS using deterministic fakes.
- Web Research tests: 7/7 PASS.
- TypeScript typecheck: PASS.
- ESLint: PASS.

## Remaining unverified behavior

- Real R2B/R2C model provider calls, authoritative usage/cost settlement, and model
  quality remain unverified by design.
- Hosted Supabase/Staging migration and browser-to-Worker E2E were not run.
- Real Web provider revalidation, redirects, DNS, and SSRF controls remain outside
  this database-only task.
- Load, corpus-scale retrieval, and production latency are not established by pgTAP.

## Readiness

The local database infrastructure is **READY for the scoped R2B/R2C Real LLM
Integration milestone**. That readiness does not authorize deployment or paid
calls: the next milestone must reserve budget before every paid boundary, use capped
test credentials, and repeat the relevant tests in an explicitly isolated Staging
environment before release.
