# Deep Research Implementation Progress

Last updated: 2026-11-04

## Safety boundary

- Work is on local branch `feat/deep-research-r2b-r2d` from `dca3eeb`.
- No hosted Supabase project is linked and no Cloud API, database, Worker, or
  Storage operation has been performed.
- Local database verification uses the repository-configured literal loopback
  endpoint only. The Phase 1 migration was applied inside a transaction and
  rolled back.
- Live provider tests and Cloud tests remain blocked unless isolated test
  credentials or explicit Staging authorization are provided.

## Phase checklist

- [x] Phase 1 — Contracts & Compatibility
  - Added strict versioned Research Request, Plan, Evidence Record, Evidence
    Assessment, and Iteration State contracts.
  - Added explicit execution transitions, graph/reference validation, objective
    preservation, permission intersection checks, and citation eligibility rules.
  - Added Deep/Legacy API and Worker routing. Deep plans cannot enter the Legacy
    Python validator or Legacy SQL RPC.
  - Added disabled-by-default `DEEP_RESEARCH_ENABLED` feature flag.
  - Added additive Deep request/account settings/plan persistence migration and
    separate `start_deep_research` RPC. Existing `start_research` is unchanged.
- [x] Phase 2 — R2B planning engine
  - Added access-scope filtering, provenance-preserving deduplication, bounded
    Context Packs, and explicit untrusted-source prompt separation.
  - Added structured planning, backend-owned stable IDs, graph/coverage quality
    gates, one repair attempt, and a bounded internal-only safe fallback.
  - Added lease-generation-fenced, idempotent plan-version persistence. Runtime
    handoff is exposed for the Phase 4 Deep Coordinator without changing Legacy.
- [x] Phase 3 — R2C evidence evaluation
  - Added deterministic ownership/provenance/validity pre-checks, content and
    lineage deduplication, and bounded untrusted evidence prompts.
  - Added six-dimensional structured evaluation, complete Requirement Coverage,
    strict ID validation, derivative-source independence correction, conflict
    classification support, conservative fallback, and ranked gaps.
  - Added separately persisted gaps plus lease-fenced, idempotent assessment
    persistence with model/token/latency/cost telemetry.
- [x] Phase 4 — R2D controller, durability, budgets, resume
  - Implemented pure deterministic action selection, internal-first ordering,
    normalized query fingerprints, coverage-based information gain, hard limit,
    quality, budget-reserve, authorization, and diminishing-return stops.
  - Added a disabled-by-default `DeepResearchCoordinator` wired to the existing
    leased Research Worker. It executes R2B/R1/R2C/R2D without a second queue,
    accumulates Evidence across rounds, resumes from durable state, and keeps
    publication behind the existing R2E/R2F verification gate.
  - Added lease-generation-fenced checkpoint CAS, operation reservation/start/
    settlement, cumulative Evidence, state reload, and no-publication terminal RPCs.
  - Added transactionally locked account-period and run budgets, a protected final
    report reserve, append-only ledger, conservative `outcome_unknown` accounting,
    idempotency-key conflict detection, and rejection when actual cost exceeds the
    atomic reservation.
  - Paid/external operations that may have crossed the side-effect boundary are not
    retried after lease takeover. Zero-cost internal operations remain safely
    idempotent. Stale workers cannot reserve, checkpoint, append, settle, or finish.
  - Added multi-round accumulation, resume/no-reexecute, uncertain-outcome, and
    stale-worker unit coverage plus 23 pgTAP durability/RLS/budget/fencing checks.
- [ ] Phase 5 — Web evidence and publication integration
- [ ] Phase 6 — Full integration and rollout readiness

## Verified tests

- Worker offline regression: 153 passed.
- Focused Deep Coordinator and R2B/R2C/R2D suite: 19 passed.
- Phase 4 Local PostgreSQL migration apply plus pgTAP: 23 passed; all three Deep
  migrations and test fixtures were executed in one verified loopback transaction
  and rolled back.
- Focused Phase 1/Legacy regression: 24 passed.
- Web Research contracts: 4 passed.
- TypeScript typecheck and ESLint: passed.
- Phase 1 migration transactional apply/rollback against verified Local
  Supabase: passed.

## Not run / blockers

- Three existing provider-backed Research tests require real LLM environment
  variables and were excluded; no credentials were loaded.
- `supabase migration up --local` is blocked by two pre-existing Local migration
  history entries (`20261030090000`, `20261030090100`) whose files are absent from
  this feature worktree. No history repair or destructive reset was performed.
  SQL validation instead applied Phase 1/3/4 and pgTAP directly to the verified
  loopback Local Postgres inside a transaction and rolled everything back.
- True multi-session budget contention is still a Phase 6 verification item. The
  Phase 4 database tests prove row-locked competing reservation limits,
  idempotency, accounting balance, stale-lease rejection, and takeover fencing;
  a parallel two-session run awaits an isolated resettable Local database.
- Staging/production migration, E2E, deployment, and paid provider calls were not
  run and are not authorized by this task.

## Next exact step

Continue Phase 5 with Web policy enforcement, run-scoped Web Evidence capture,
source lifecycle handling, verified partial-result persistence, and Web citation
publication without weakening the existing R2E/R2F gate.
