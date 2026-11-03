# Deep Research Implementation Status

Last updated: 2026-11-04

This document supersedes earlier claims that Phases 1–6 were fully verified.
The current repair branch is `fix/deep-research-infrastructure`, based on
`7c0a4ea`. No hosted Supabase resource, provider, deployment, or production
credential was used during the repair.

## Verification matrix

| Area | IMPLEMENTED | UNIT_TESTED | LOCAL_DB_VERIFIED | RUNTIME_INTEGRATED | PROVIDER_VERIFIED | STAGING_VERIFIED | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Phase 1 migration integrity | Authentic missing migrations restored from local Git history | File identity/history checked | No; disposable Supabase is unavailable | Migration chain is present | N/A | No | BLOCKED on clean replay |
| Phase 2 operation recovery | Additive recovery and settlement RPC replacements | Worker crash/resume and replay tests pass; pgTAP authored | No | Worker already uses the repaired RPC contracts; migration is not applied | No | No | IMPLEMENTED, DB verification blocked |
| Phase 3 budget ledger | Original-period settlement, explicit release, idempotent settlement, atomic locks | Synthetic accounting pgTAP and two-session `dblink` test authored | No | Persistence boundary is implemented; future provider cost reporting is not | No | No | IMPLEMENTED, concurrency run blocked |
| Phase 4 limits/state/replay | Durable deadline, query hard stop, truthful terminal states, idempotent publication wrapper | Coordinator tests pass; pgTAP authored | No | Coordinator source path is integrated; migration is not applied | No | No | IMPLEMENTED_BUT_NOT_INTEGRATION_TESTED |
| Phase 5 Web/API integrity | Deep result/Web citation reads, validity mapping, publication lifecycle checks | Web tests pass; RLS pgTAP authored | No | Authenticated API read path is implemented | No real Web adapter by scope | No | IMPLEMENTED_BUT_NOT_INTEGRATION_TESTED |
| Phase 6 offline validation | Test fixtures and commands exist | Worker/Web offline suites pass | No | N/A | No | No | PARTIAL |

## Implemented behavior

- Legacy Research remains on its existing RPC and read path. Legacy reads do not
  query Deep-only tables.
- A reservation abandoned before `start` can transfer to the new lease generation
  without duplicating its budget or ledger entry. A paid operation that reached
  `running` becomes `outcome_unknown` instead of being retried automatically.
- Settlement uses the reservation's persisted accounting month, records actual and
  released amounts separately, and is idempotent after a terminal transition.
- Every Deep run has a persisted deadline. The database prevents new operations
  and operation starts after it, while the coordinator rechecks it after restart
  and between phases. Post-deadline synthesis/publication is not started.
- `maxQueries` is enforced by both coordinator round count and database operation
  count. Existing max-round/controller behavior is unchanged.
- Verified partial, insufficient, timed-out, failed, cancelled, and draft-ready
  states remain distinguishable. A verified partial does not create a Note or
  claim `draft_ready`.
- Exact publication replay returns the existing Note after a lost response. A
  conflicting replay fails, and authenticated clients have no execute privilege.
- The authenticated research GET path returns persisted Deep result metadata and
  internal/Web citation provenance. Missing, deleted, superseded, or ineligible
  current Web Evidence is never presented as valid.

## Current test evidence

- Worker offline suite, excluding three provider-executing collection scripts and
  the sandbox-socket HTTP module: `157 passed in 0.39s`.
- Web offline suites: `34 passed` total across seven commands.
- Deep Web read tests: `7 passed`.
- TypeScript `tsc --noEmit`: passed.
- ESLint: passed.
- Worker HTTP tests: `8 failed` during setup because the sandbox denied
  `socket.bind(127.0.0.1, 0)`; no assertion or product path ran.
- Next production build: blocked only while `next/font` attempted to resolve
  `fonts.googleapis.com` in the network-restricted environment.
- Local migration replay, pgTAP, RLS/RPC execution, and the two-session budget
  test: not run. Supabase Local cannot access the Docker socket and no disposable
  PostgreSQL endpoint is listening on the configured local ports.

## Explicitly unverified

- SQL parse/apply against a clean complete schema and all historical migrations.
- Runtime RLS behavior and RPC grants on PostgreSQL.
- Actual two-session budget serialization and stale-lease takeover on PostgreSQL.
- Real provider cost coverage, Web source revalidation, DNS/redirect/SSRF behavior,
  model quality, browser E2E, load, and production latency.
- Any Staging or hosted migration/application behavior.

## Next safe milestone

Before R2B/R2C real LLM integration, apply the complete migration chain to a newly
created isolated database, run all pgTAP files including
`deep_research_budget_concurrency.test.sql`, and resolve any SQL/runtime failures.
Only after that should isolated Staging credentials and capped provider tests be
considered. Real provider integration is not part of this repair.
