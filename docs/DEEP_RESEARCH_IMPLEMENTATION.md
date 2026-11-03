# Deep Research Implementation Progress

Last updated: 2026-11-03

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
- [ ] Phase 2 — R2B planning engine
- [ ] Phase 3 — R2C evidence evaluation
- [ ] Phase 4 — R2D controller, durability, budgets, resume
- [ ] Phase 5 — Web evidence and publication integration
- [ ] Phase 6 — Full integration and rollout readiness

## Verified tests

- Worker offline regression: 140 passed.
- Focused Phase 1/Legacy regression: 24 passed.
- Web Research contracts: 4 passed.
- TypeScript typecheck and ESLint: passed.
- Phase 1 migration transactional apply/rollback against verified Local
  Supabase: passed.

## Not run / blockers

- Three existing provider-backed Research tests require real LLM environment
  variables and were excluded; no credentials were loaded.
- Docker-dependent `supabase test db` was not run because the Docker socket is
  unavailable. SQL migration validation used direct Local Postgres transaction
  rollback instead.
- Staging/production migration, E2E, deployment, and paid provider calls were not
  run and are not authorized by this task.

## Next exact step

Implement Phase 2 R2B Context Pack, strict Deep Planner, quality gate,
single-repair/fallback behavior, and fenced versioned plan persistence.
