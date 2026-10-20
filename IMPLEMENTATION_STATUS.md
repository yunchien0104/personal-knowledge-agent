# Personal Brain Implementation Status

## Current phase

- Phase 1 / Cloud Staging Isolation Test Adapter — adapter and offline safety
  guardrails complete; no Cloud write test was executed.

## Completed

- Existing Next.js App Router project and Python 3.13 virtual environment preserved.
- Safe local environment template added; no credentials or remote connections.
- Modular Python worker package, handler protocol, registry, and executable runner added.
- Supabase local configuration and empty migrations directory added.
- Dependency-free Web and Worker test commands added.
- Versioned Job Payload v1 schema and fixture added with TypeScript/Python validators.
- Cross-language normalization test added.
- Root/web instruction precedence and root canonical spec path clarified.
- Added Assets, Revisions, Artifacts, Evidence, Notes, and Note Assets migrations.
- Added UUID keys, ownership-preserving composite foreign keys, constraints,
  timestamps, indexes, explicit deletion behavior, and forced RLS.
- Restricted authenticated writes to user-editable fields; revisions, artifacts,
  evidence, asset status, and revision promotion remain backend-managed.
- Added the private `asset-private` bucket and path-scoped Storage policies.
- Added repeatable pgTAP tests with two `auth.users` identities and distinct JWT
  subjects for CRUD, cross-user FK, derived-data, and Storage isolation.
- Added migration rollback order, data-loss warnings, and recovery guidance.
- Added a repeatable standard-library Storage API integration test with two real
  Auth sessions, unique per-run prefixes, and API-based cleanup.
- Added a narrowly scoped DELETE policy for owned `original` objects; derived
  objects remain backend-managed and cannot be deleted by authenticated clients.
- Added pinned official Supabase SSR/client packages, cookie-backed browser/server
  clients, and a Next.js 16 session-refresh proxy for protected routes.
- Added minimal register, sign-in, sign-out, and protected-page flows. Protected
  server rendering revalidates the Auth user and never accepts caller `user_id`.
- Added a repeatable local Auth integration test with unique test accounts and
  Auth Admin API cleanup; the service secret is used only by the test cleanup.
- Verified the linked Staging migration history matches all three local migrations.
- Verified all six user-data tables exist with RLS and forced RLS enabled, the
  expected policies exist, and `asset-private` is a private Storage bucket.
- Extended the existing Storage API isolation runner with an explicit
  `--allow-staging` mode while preserving the default Local-only entry point.
- Added exact HTTPS project URL/ref validation, separate Staging allowlist and
  Production denylist, same-origin redirects, environment-only Cloud secrets,
  confirmed Admin-created users, immediate resource tracking, and retry cleanup.

## Task 2 test results

- PASS: static schema/security assertions (6 tables, forced RLS, policy checks).
- PASS: Web tests (2), TypeScript typecheck, and Worker tests (2).
- PASS: user confirmed both migrations applied successfully to local Supabase.
- PASS: `supabase test db` completed 45/45 pgTAP assertions with a complete
  explicit test plan, including database and Storage authorization behavior.
- PASS: `supabase db lint --local --level warning --fail-on error` found no
  schema errors.

## Task 2.1 test results

- PASS: Storage API integration test completed 18/18 assertions: private bucket,
  two Auth sessions, own upload/download/list/delete, cross-user read/list/
  overwrite/delete denial, and unauthenticated denial.
- PASS: Storage objects, temporary Assets, and temporary Auth users were removed
  through local APIs after the test.
- PASS: existing `supabase test db` remained 45/45; DB lint found no errors.

## Task 3A test results

- PASS: Auth integration completed 7/7 assertions: anonymous rejection,
  registration, password login, sign-out invalidation, forged `user_id` ignored,
  corrupted-session rejection, and temporary Auth user cleanup.
- PASS: Web production build, TypeScript typecheck, ESLint, and existing Web tests (2).
- PASS: existing database/Storage security pgTAP suite remained 45/45.

## Cloud Staging verification

- PASS: linked migration history matches local versions `20261020090000`,
  `20261020090100`, and `20261020150127` exactly.
- PASS: Assets, Revisions, Artifacts, Evidence, Notes, and Note Assets exist;
  all six have RLS and forced RLS enabled with expected policy counts.
- PASS: `asset-private` exists with `public = false`; four scoped object policies exist.
- PASS: linked DB lint found no schema errors and security advisors found no issues.
- BLOCKED pending user approval: live two-user Staging Auth/RLS/Storage API test.

## Cloud Staging adapter verification

- PASS: Python compilation and 10/10 offline Cloud guardrail tests, including
  a structural assertion that the integration suite still contains all 18 checks.
- PASS: Cloud mode fails closed without all required environment variables;
  no Cloud request was made.
- BLOCKED: post-change Local 18-assertion runtime regression because this Codex
  sandbox cannot access the OrbStack Docker socket. The test did not reach an API.
- NOT RUN by explicit instruction: Cloud Staging write/isolation test.

## Not completed

- Product API routes, queue leasing/fencing, ingestion adapters, RAG, and research.
- Deployment and paid/external API integration.
- Coordinated Asset/Storage deletion and citation tombstoning; direct Asset deletion
  is owner-scoped, but Storage cleanup requires the later protected backend workflow.

## Assumptions and limitations

- Job Payload v1 is a minimal envelope; job-specific `input` schemas will be versioned later.
- `healthcheck` is a baseline runner handler, not a product workflow.
- Validators use standard-library/manual validation to avoid new dependencies.
- Web production build uses Next.js's supported Webpack mode because Turbopack cannot
  bind its CSS-processing port in the current sandbox.
- Direct SQL deletion from `storage.objects` is rejected by Supabase's
  `storage.protect_delete()`; production cleanup must use the protected Storage API.
- Authenticated clients can now delete their own original object directly. Product
  deletion should still use a coordinated backend workflow so Asset state,
  citations, and Storage cleanup cannot diverge.

## Next action

- Re-run the Local 18-assertion test from a shell with OrbStack access, then set
  the six Staging-only environment variables and invoke `--allow-staging` after
  explicit authorization for Cloud writes. Do not begin Task 3B.
