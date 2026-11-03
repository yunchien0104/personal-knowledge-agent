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
- [x] Phase 5 — Web evidence and publication integration
  - Added explicit `disabled`, `suggest`, and `auto` execution semantics. Only
    `auto` with both discovery and fetch permissions can call a Web provider;
    `suggest` remains an authorization boundary and never executes automatically.
  - Added credential-free HTTPS canonicalization, direct private/special IP and
    localhost rejection, bounded discovery/fetch results, and a provider protocol
    suitable for isolated mocks or an explicitly configured production adapter.
  - Search snippets are recorded as non-citation-eligible inputs. Only fetched,
    content-hashed, run-scoped Web Evidence with exact locators can support claims.
  - Added forced-RLS Web source, Evidence, verified result, and citation snapshot
    tables plus lease/operation-fenced capture and source-status RPCs.
  - New content supersedes earlier versions. Deleted/unavailable sources invalidate
    every captured version and are rechecked transactionally at publication.
  - Added verified partial-result persistence without creating a Note. Complete
    publication still requires the existing R2E/R2F all-claims-supported gate;
    mixed internal/Web citations use a separate Deep publication RPC and immutable
    Web provenance snapshots. Legacy publication remains unchanged.
- [x] Phase 6 — Full local verification (rollout/staging gates remain explicit)
  - Worker offline regression, focused multi-round/crash-resume tests, Web unit
    tests, Web TypeScript/ESLint/build, Deep migration execution, RLS/RPC pgTAP,
    source lifecycle, positive/negative publication, and Golden Dataset fixture/
    scorer checks pass locally.
  - Local Deep tests use only deterministic fakes and zero-cost adapters. No paid
    provider call, hosted database, Storage, Worker, deployment, or production
    credential was used.
  - Release readiness is intentionally not claimed: provider-backed quality,
    multi-session contention, corpus-scale retrieval, browser-to-publication
    Staging E2E, load, DNS/redirect enforcement, and real cost/latency remain the
    staging verification list below.

## Verified tests

- Worker offline regression: 161 passed.
- Focused Deep Coordinator and R2B/R2C/R2D suite: 19 passed.
- Phase 4 Local PostgreSQL migration apply plus pgTAP: 23 passed; all three Deep
  migrations and test fixtures were executed in one verified loopback transaction
  and rolled back.
- Phase 5 Web policy/provenance unit tests: 8 passed.
- Phase 5 Local PostgreSQL Web lifecycle/publication pgTAP: 27 passed; migrations
  and fixtures were executed in the same verified loopback rollback boundary.
- Web local verification: TypeScript passed; ESLint passed; Next production build
  passed; 31 offline Web tests passed across job payload, inbox, Source Notes,
  Retrieval, Research, Threads, and Instagram/Reel paths.
- Golden fixtures/scoring: 20-case claim-verification oracle scored 100% accuracy
  and 0% false acceptance, validating dataset/scorer wiring only; the retrieval
  Golden Dataset threshold/metric fixture test passed. These are not live model
  quality claims.
- Local timing baseline: full 161-test Worker suite completed in 4.92s; focused
  Deep coordinator/Web suite completed in 0.16s wall time (12 tests); Next build
  completed in 4.91s. Local mock/provider cost was exactly $0. These timings are
  developer-machine regression timings, not production latency or capacity.
- Focused Phase 1/Legacy regression: 24 passed.
- Web Research contracts: 4 passed.
- TypeScript typecheck and ESLint: passed.
- Phase 1 migration transactional apply/rollback against verified Local
  Supabase: passed.

## Not run / blockers

- Four provider-backed/live Research test modules require real provider settings
  or a live pipeline and were excluded; no provider credentials were loaded.
- `supabase migration up --local` is blocked by two pre-existing Local migration
  history entries (`20261030090000`, `20261030090100`) whose files are absent from
  this feature worktree. No history repair or destructive reset was performed.
  SQL validation instead applied Phase 1/3/4 and pgTAP directly to the verified
  loopback Local Postgres inside a transaction and rolled everything back.
- True multi-session budget contention is still a Phase 6 verification item. The
  Phase 4 database tests prove row-locked competing reservation limits,
  idempotency, accounting balance, stale-lease rejection, and takeover fencing;
  a parallel two-session run awaits an isolated resettable Local database.
- No external Web provider adapter is enabled by default. Phase 5 uses a strict
  injected provider interface and local mocks; live discovery/fetch remains blocked
  until an allowlisted provider and isolated test credentials are explicitly
  configured. DNS resolution and redirect enforcement belong in that adapter in
  addition to the coordinator's URL checks.
- Full Supabase `test db`, all historical pgTAP protocols, and a true two-session
  budget race were not run because the existing Local migration history references
  two migration files absent from this feature worktree. Repair/reset would mutate
  unrelated local state, so only the additive Deep migrations and 50 new pgTAP
  assertions were run transactionally and rolled back.
- Remaining Staging verification: apply migrations to an explicitly designated
  isolated project; run all RLS/RPC/Storage protocols; test concurrent reservations
  and stale Worker takeover across sessions; configure an allowlisted Web provider;
  verify redirect/DNS/IP controls; run paid-provider quality on held-out data with
  a hard spend cap; run browser-to-publication E2E; measure p50/p95/p99, ANN recall,
  cost, and load at documented corpus/concurrency sizes.
- Staging/production migration, E2E, deployment, and paid provider calls were not
  run and are not authorized by this task.

## Next exact step

Next action requires user-controlled Staging/provider authorization; do not deploy,
push migrations, or make paid calls from this local implementation task.
