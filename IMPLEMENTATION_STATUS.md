# Personal Brain Implementation Status

## Current phase

- R1 Unified Hybrid Retrieval is implemented locally and awaits isolated Staging
  migration, pgvector protocol tests, real embedding-provider verification, and
  incremental backfill. No Cloud resource was changed in this implementation run.

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
- Verified the linked Staging migration history matched the three foundation
  migrations before Step 4A; the new queue migration remains local-only and unapplied.
- Verified all six user-data tables exist with RLS and forced RLS enabled, the
  expected policies exist, and `asset-private` is a private Storage bucket.
- Extended the existing Storage API isolation runner with an explicit
  `--allow-staging` mode while preserving the default Local-only entry point.
- Added exact HTTPS project URL/ref validation, separate Staging allowlist and
  Production denylist, same-origin redirects, environment-only Cloud secrets,
  confirmed Admin-created users, immediate resource tracking, and retry cleanup.
- Added authenticated, RLS-backed Asset list/create/read and Note list/read Route
  Handlers with consistent JSON envelopes, no-store responses, Zod validation,
  bounded offset pagination, and caller-supplied `user_id` rejection.
- Added server-only Supabase runtime overrides so production builds can be tested
  against localhost without relying on build-time `NEXT_PUBLIC_*` substitution.
- Added a Local-only HTTP integration suite using two real Auth sessions. Data
  fixtures and cleanup use each user's JWT; the local secret is restricted to
  creating and deleting temporary Auth identities.
- Added authenticated PDF upload preparation/completion/cleanup using short-lived
  signed uploads, fixed per-user immutable object paths, and the existing private
  `asset-private` bucket and Asset RLS.
- Added client and server PDF validation: `.pdf` extension, `application/pdf` MIME,
  10 MiB maximum, exact expected size, and `%PDF-` content signature.
- Added compensating cleanup for signing, upload, verification, and abandoned-flow
  failures; object deletion occurs before Asset deletion to preserve Storage RLS.
- Added protected Traditional Chinese PDF Upload and Asset Inbox pages. Inbox shows
  filename, Asset status, creation time, verified Storage presence, and a five-minute
  signed download link so the user can confirm the private file was preserved.
- Kept the Supabase factory server-only. Browser uploads use only the short-lived
  signed Storage URL returned by the authenticated API, with HTTPS/localhost,
  exact Storage-path, no-redirect, and no-JWT checks before sending the PDF.
- Added user-owned Jobs and Job Attempts with forced RLS, least-privilege grants,
  per-user idempotency, bounded attempts, indexed availability/lease lookups, and
  ownership-preserving Asset foreign keys.
- Added an ownership-checked PDF enqueue RPC plus service-role-only atomic claim,
  heartbeat, expired-lease reclaim, fenced failure, exponential-backoff retry,
  and permanent/unsupported failure RPCs.
- PDF completion now enqueues one durable `ingest_pdf` Job with the versioned v1
  payload after Storage verification; safe completion replays return the same Job.

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

## Task 3B test results

- PASS: HTTP Product API integration completed 5/5 tests, covering all five
  endpoints, anonymous denial, malformed input, pagination, forged `user_id`,
  normal operations, two-user RLS isolation, and exact fixture cleanup.
- PASS: Web production build, TypeScript typecheck, ESLint, Web contract tests
  (2/2), and existing Auth integration tests (7/7).
- PASS: existing Worker tests (2/2) with `PYTHONPATH=worker/src`.
- PASS: existing database/Storage authorization pgTAP suite remained 45/45.
- NOT RUN: Cloud Staging API tests; no Product API data was written to Staging.

## PDF Upload vertical-slice test results

- PASS (before the final signed-URL client transport hardening): PDF upload
  integration completed 5/5 tests: anonymous denial, metadata validation, signed
  upload, server content verification, owner Inbox/download, cross-user isolation,
  private anonymous denial, invalid upload compensation, abandoned upload cleanup,
  and exact test-fixture cleanup.
- PASS: Web production build, TypeScript typecheck, and ESLint.
- BLOCKED: the post-hardening 5-test Local integration regression could not reach
  Supabase because this Codex sandbox is denied access to the OrbStack Docker
  socket. `supabase status -o json` fails before any API request or fixture write.
- NOT RUN by scope: unchanged Product API/Auth/Worker/pgTAP suites.
- NOT RUN by instruction: Cloud Staging and Vercel deployment tests.

## Step 4A durable queue test results

- PASS: Web TypeScript typecheck, ESLint, and production build.
- PASS: linked Project Ref exactly matched the configured Staging URL. Remote and
  local migration history match after applying
  `20261021034022_durable_pdf_ingestion_queue.sql`.
- PASS: all 44 Queue protocol pgTAP assertions executed through the CLI remote SQL
  channel; the final TAP result was `ok 44` with a complete plan and no failure.
- PASS: five concurrent-claim conditions were verified with two simultaneous HTTPS
  requests and independent database transactions. Exactly one request claimed the
  Job; the database recorded one winner, one attempt, and lease generation one.
- PASS: the remote concurrency fixture used reserved IDs verified absent before the
  test. Its temporary RPC, Auth user, Asset, Job, and Attempt all verified absent
  after exact cleanup.
- PASS: linked `supabase db lint --schema public --level error --fail-on error`
  reported no schema errors.
- PASS: `supabase/QUEUE_ROLLBACK.md` now documents only the Step 4A RPCs,
  `job_attempts`, and `jobs`, including dependencies, data-loss risk, and a
  no-`CASCADE` safe removal order. The foundation rollback guide no longer mixes
  queue removal into its procedure.
- NOTE: `supabase test db --linked` still attempts to start a Docker pgTAP runner,
  and the standalone `dblink` test cannot authenticate a nested Cloud connection.
  Acceptance therefore used the CLI remote SQL channel plus a temporary,
  service-role-only hold RPC; neither limitation affected the tested queue behavior.

## Step 4B native PDF parser results

- Added pinned `pypdf==6.21.1` and a bytes-in/structured-result-out parser that
  performs no Storage or database access.
- Native text is normalized per page and emitted with 1-based page numbers,
  document offsets, SHA-256 source identity, and `pdf_page` Evidence locators.
- Encrypted, corrupt, scanned/image-only, blank, and resource-limit failures have
  stable safe error codes and never synthesize text.
- PASS: six focused parser tests plus two existing Worker tests (8/8 total).
- PASS: rendered fixtures visually confirm two text pages, an image-only page,
  and a blank page match their intended test categories.

## Not completed

- Production model/provider acceptance for Source Note generation, semantic/vector
  retrieval, and Research Agent workflows.
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
- Before the server-only override was added, two integration attempts may have sent
  random-email sign-up requests to the build-configured Staging Auth endpoint; no
  session was obtained. No Product API, database, or Storage request targeted
  Staging. Confirm the Staging Auth user list before any user-authorized cleanup.
- The signed-upload flow compensates normal failures, but a browser closed after
  preparation can leave a pending Asset until a future bounded stale-upload cleanup
  job exists. It does not expose a file or another user's data.
- PDF upload is conservatively limited to 10 MiB. Original filenames and expected
  sizes are encoded in the existing PDF `source_external_id`; no schema change was
  introduced for this MVP slice.
- Step 4C uploads deterministic immutable artifacts before DB promotion. A Worker
  that permanently loses its lease after upload can leave an unreachable immutable
  object; bounded orphan reconciliation remains operational backlog and cannot
  corrupt the active Revision pointer.

## Step 4C PDF ingestion and publication results

- Added a server-only Worker gateway for Queue RPCs and private Storage, with
  redirect rejection, validated object paths, immutable uploads, and safe errors.
- Added claim, background/checkpoint heartbeat, temporary-file cleanup, native PDF
  parsing, failure classification, retry handling, and fenced publication flow.
- Added an atomic publication RPC that validates lease owner/generation/expiry,
  creates immutable Revision/Artifact/page Evidence, promotes the active Revision,
  and completes the Job/Attempt. Exact replay returns the same Revision.
- Updated failure handling to retain an existing active Revision and ready Asset.
- Inbox now shows queued, processing, completed, or failed plus allowlisted safe
  error messages; backend error text is not rendered.
- PASS: Worker tests 13/13; Inbox tests 2/2; TypeScript, ESLint, and Web build.
- PASS: Cloud Staging migration `20261021055829_fenced_pdf_publication.sql` is
  deployed; local and remote migration history match. A PostgreSQL parser error in
  the offset continuity expression was fixed by parenthesizing its `case` value.
- PASS: remote publication pgTAP completed 25/25 assertions with zero failures and
  rolled back all fixture rows. This covers exact RPC grants, stale-lease fencing,
  idempotent replay, and preserving a successful Revision after a later failure.
- PASS: isolated Cloud E2E completed Auth session → Asset/original Storage object →
  Job claim/heartbeat → Storage download → native PDF parse → immutable Artifact
  upload → fenced Revision/Artifact/two-page Evidence publication. A repeat Worker
  run was idle and created no duplicate results.
- PASS: Cloud DB lint reports no `public` schema errors. The exact test Auth user,
  Asset, Job/Attempt, Revision, Artifact, Evidence, and both Storage objects were
  removed through Auth/Storage APIs; a final database query confirmed every count
  was zero.

## Step 5A AI Document Understanding and Source Notes

- Added additive Source Note provenance, Claims, and claim-level Evidence citation
  schema with owner-scoped read-only RLS for generated records. Notes remain
  independently editable and retain citation snapshots if source rows are deleted.
- Added idempotent authenticated enqueue, lease-fenced source context retrieval,
  atomic Source Note publication, and optimistic Note editing RPCs. General users
  cannot publish Claims or citations directly.
- Added a configurable server-only OpenAI-compatible Structured Output adapter and
  `llm` Worker capability. The Worker reads the active normalized PDF artifact,
  validates its fingerprint/page Evidence, and rejects unknown Evidence IDs or
  non-verbatim support quotes before publication.
- Generated Claims are always stored as `unreviewed` with `needs_review = true`;
  same-source assertions are never mislabeled as independently corroborated.
- Added minimal Generate/View/Edit UI with safe job errors, model/source provenance,
  optimistic edit conflict handling, and signed PDF links anchored to citation pages.
- PASS: Mock LLM and affected Worker tests 12/12; Source Note Web tests 2/2;
  TypeScript, ESLint, and production build.
- PASS: Step 5A migrations are deployed to Cloud Staging. A follow-up additive
  migration enables `llm` Queue claims without downgrading ready Assets and another
  resets Source Note RPC grants to the exact intended API roles.
- PASS: remote Source Note protocol pgTAP completed 22/22 assertions, including
  least-privilege RPC grants and preserving ready Asset state during LLM claims.

## Step 5B Knowledge Memory & Retrieval

- Added tenant-scoped PostgreSQL Full-Text Search indexes and the bounded
  `search_knowledge` RPC across Source Notes, Claims, and original Evidence.
- Results explicitly identify `generated_note`, `generated_claim`, or
  `original_source`, with owned Asset/Revision IDs and Evidence locator snapshots
  retaining PDF page numbers. No vectors, embeddings, or paid model calls exist.
- Added an authenticated reusable retrieval module/API plus a protected minimal
  search page with signed source-PDF links anchored to citation pages.
- PASS: Cloud migrations through `20261021113000` match local history; remote
  retrieval pgTAP completed 25/25 and Cloud DB lint found no schema errors.
- PASS: real Cloud Auth/RLS integration completed 9/9 assertions with two users;
  cross-user Assets/Evidence were excluded and anonymous RPC access was denied.
- PASS: all isolated Cloud users and database fixtures were precisely removed; an
  ID recorded before the initial local CA failure was separately confirmed absent.
- PASS: retrieval tests 3/3, Python test compilation, TypeScript, ESLint, and Web
  production build. No Step 4 suites, Docker, Render/Vercel deploy, or commit ran.

## Step 6 Research Agent MVP

- Added explicit authenticated Research Runs and a dedicated `research` Queue
  capability. Research is never started by background events and is not coupled to
  a single Asset.
- Added bounded plan validation, tenant-scoped active original-Evidence retrieval,
  deterministic Evidence selection, grounded synthesis, and lease-fenced atomic
  Research Draft publication. The MVP has `maxCostUsd = 0`, one retrieval round,
  no Web search, and no paid model call.
- Added read-only owner RLS for Research Runs/citations, exact Data API grants,
  immutable Evidence/Asset/Revision/locator/quote snapshots, and server-side
  revalidation of active original Evidence before publication.
- Added authenticated Research API and minimal UI showing plan, progress, safe
  errors, draft content, raw quotes, immutable IDs, and short-lived PDF page links.
- PASS: Mock Research Worker tests and full Worker suite 23/23; Research Web tests
  3/3; affected Retrieval/Source Note tests 5/5; TypeScript, ESLint, Web build.
- PASS: Cloud Staging migration `20261021120000_research_agent_mvp.sql` is deployed
  and local/remote history match. The first syntax-check attempt rolled back fully;
  the corrected migration then applied successfully.
- PASS: remote Research protocol pgTAP completed 27/27 in a rollback transaction,
  including grants, forced RLS, user-scoped retrieval, stale lease fencing,
  verbatim citation rejection, draft-only publication, replay, and cross-user denial.
- PASS: linked Cloud DB lint found no `public` schema errors.

## Step 6 first browser E2E

- PASS: a real browser signed in with a confirmed isolated Staging test account,
  uploaded the two-page `native-text.pdf`, observed Inbox processing complete,
  generated and viewed a Source Note, submitted internal research, and opened a
  `draft_ready` Research Draft.
- PASS: the Source Note showed verbatim Claims for pages 1 and 2. The Research
  Draft retrieved `Second page evidence.`, preserved its Evidence and Revision
  IDs, and exposed a private signed PDF citation whose browser navigation target
  ended in `#page=2`.
- PASS: the existing Worker completed `ingest_pdf`, `generate_source_note`, and
  `execute_research` Jobs against Cloud Staging. Source Note generation used the
  explicit zero-cost `SOURCE_NOTE_MODEL_MODE=local-extractive` E2E adapter; the
  default production mode remains `openai-compatible`.
- FIXED: signed Storage uploads now use the `PUT` method required by Supabase's
  signed-upload endpoint. The previous `POST` failed before completion and its
  compensating cleanup correctly left no Asset, Job, or object.
- FIXED: Research request-key generation now retains `crypto.randomUUID()` on
  secure origins and has a UUID-format fallback for local-network HTTP browser
  verification, where Web Crypto is unavailable. The key is idempotency metadata,
  not an authentication credential or secret.
- PASS: the production Web build completed after both browser blockers were
  corrected. Previously passing unit suites were not rerun, per acceptance scope.
- PASS: cleanup removed exactly the original PDF and immutable normalized-text
  object, then deleted the isolated Auth user. All 13 user-owned data tables and
  the Storage prefix verified zero rows/objects; the Admin Auth lookup returned 404.
- DEPLOYMENT GAP: the repository has no linked Vercel project or Render/other
  Worker deployment configuration, and the Step 1-6 implementation is not present
  in a deployable Git revision. This acceptance therefore verifies the real browser
  and Cloud data path, but not a hosted Web/Worker deployment.
- FIXED: `/protected` now establishes a real-request boundary with Next.js
  `connection()` before Supabase `auth.getUser()`. This prevents the Auth SDK's
  session-expiry `Date.now()` check from running during Cache Components
  prerendering while preserving the existing authenticated render and anonymous
  redirect. TypeScript, ESLint, production build, and an anonymous 307 runtime
  redirect check passed without a blocking-route error.

## Step 6B browser E2E navigation acceptance

- FIXED: the authenticated landing page now exposes a prominent `Upload PDF`
  link to the existing real `/upload` flow. The Upload, Inbox, Storage upload,
  ingestion Queue, Source Note, Research Agent, and RLS implementations were
  reused unchanged; no mock upload or schema/RLS change was added.
- FIXED: `/upload`, `/inbox`, `/assets/[id]/source-note`, and `/search` now establish
  the same request boundary as `/protected` before Supabase session access. This
  removes the Auth SDK `Date.now()` blocking-prerender errors observed during link
  prefetch while retaining authenticated redirects and Cache Components elsewhere.
- PASS: a fresh isolated Cloud Staging account completed real-browser login,
  `/protected`, browser refresh with the same session, the visible Upload link,
  two-page PDF upload, Worker ingestion, completed Inbox, Source Note generation,
  and a `draft_ready` internal Research Draft grounded in page 2 Evidence.
- PASS: clicking the draft citation opened the signed original PDF in a new browser
  tab with a target URL ending in `#page=2`; logout returned to the sign-in page.
- PASS: TypeScript, ESLint, production build, and targeted production-runtime route
  checks completed without blocking-route output. Previously passing unit suites
  were not rerun, as required by this acceptance scope.
- PASS: cleanup deleted exactly two test Storage objects and the isolated Auth user;
  all 13 user-owned tables and the user's Storage prefix verified zero residue, and
  the post-delete Admin Auth lookup returned 404.
- UNCHANGED DEPLOYMENT GAP: no Web or Worker deploy was performed. The existing
  repository deployment gap remains documented above.

### Latest full Step 6B regression

- PASS: the latest production build completed a fresh real-browser regression on
  Cloud Staging with two confirmed, isolated test accounts and the existing
  two-page `native-text.pdf` fixture.
- PASS: Account A signed in to `/protected`, retained its authenticated session
  after a browser reload, uploaded the PDF, and received a real private Storage
  object. The Worker claimed and completed ingestion, published an active validated
  Revision, and the Inbox changed to completed.
- PASS: Source Note generation completed through the Worker and rendered both
  verbatim page Claims in the UI. Internal Research produced a `draft_ready` note
  for `Second page evidence.`, linked it to the immutable Evidence and Revision,
  and opened the signed private PDF URL with a `#page=2` fragment.
- PASS: after logout, navigating to `/protected` returned the sign-in page. Account
  B had an empty Inbox and no Research Runs, received 404 for Account A's direct
  Source Note URL, and saw zero Account A rows through authenticated Data API
  queries across Asset, Revision, Evidence, Note, and Research Run boundaries.
  Its authenticated Storage listing under Account A's prefix was also empty.
- PASS: browser console and Web server output contained no runtime or Blocking
  Route errors. Related Web tests passed 7/7, related Worker tests passed 15/15,
  and TypeScript, ESLint, and the production build passed.
- PASS: both test users were signed out and deleted. Exact Storage objects were
  removed; both user IDs verified zero rows across all 13 user-owned tables, zero
  Storage prefix residue, and 404 from the Admin Auth lookup. Temporary credentials
  were removed. No product-code correction was required by this regression.
- READY: Step 6B acceptance is complete and development can proceed to Step 7.

## Step 7A Research Suggestion Engine

- Implemented a deterministic, zero-provider-cost Opportunity ranker over active
  Source Note Claims and their active original Evidence. It uses claim gaps,
  verification/review state, evidence availability, recency, prior completed
  Research questions, and existing Opportunity cooldowns; tenant identity is
  validated again in the Worker before ranking.
- Ranking is deterministic and explainable, rejects scores below 60, deduplicates
  semantic claim keys, and returns at most 10 new suggestions per user/day.
  Repeated signals update the existing suggestion after cooldown instead of
  creating another card.
- Added the `opportunity` Queue capability and fenced context/publication gateway.
  `RESEARCH_SUGGESTIONS_ENABLED` defaults to `false`; the Opportunity Worker is
  unavailable until explicitly enabled. Feedback changes only suggestion state
  and cooldown and cannot create a Research Run or `execute_research` Job.
- Added an undeployed Migration-as-Code draft for owner-forced RLS, explicit Data
  API grants, daily idempotent ranking Jobs, immutable feedback history, atomic
  Asia/Taipei daily automatic spend enforcement at US$0.50, score/day limits, and
  Worker lease fencing. Added a forward-rollback runbook and pgTAP protocol test.
- PASS: Opportunity unit tests 11/11 cover threshold/determinism, maximum 10 new,
  deduplication, feedback cooldown, shared cost cap, tenant mismatch, default-off
  feature flag, Queue contract, and the invariant that ranking never starts
  Manual Research.
- PASS: full Worker suite 34/34 and Python compile; Web tests 12/12, TypeScript,
  ESLint, and Production Build. No Docker, deployment, paid model, or Cloud Schema
  mutation was used.
- BLOCKED BY REQUIRED APPROVAL: the new schema is a necessary prerequisite and
  `20261021145113_research_suggestion_engine.sql` remains intentionally undeployed.
  Therefore the new pgTAP database protocol (forced RLS/two-user isolation,
  feedback-only behavior, budget constraint, and database deduplication) cannot
  truthfully be reported as executed against Cloud Staging yet.
- API/UI GATE: the Worker/ranking layer is ready for the recommendation API/UI
  phase only after review and approved Cloud Staging application of the migration,
  followed by the new pgTAP protocol and linked database lint. No recommendation
  API or UI was added in Step 7A.

## Next action

- Review the Step 7A migration and rollback runbook. After explicit approval,
  apply the migration to Cloud Staging, run the new pgTAP protocol plus linked DB
  lint, then add authenticated Opportunity list/feedback API routes and the minimal
  suggestion UI while keeping Manual Research start an explicit user action.

## V6 UI migration to Next.js App Router (2026-10-22)

- Ported Login, Home, Library, Explore, Ask, Research, and the shared AppShell into
  `web/src/app/(workspace)` and `web/src/components/personal-brain`.
- Replaced React Router navigation with Next.js App Router routes while preserving
  the existing application API, Supabase, Worker, and database contracts.
- Wired Supabase Auth for sign-in, sign-up, email verification/resend, password
  recovery/reset, and sign-out. Demo credentials and fake sessions were removed.
- Connected the UI to real Assets, Notes, Retrieval, Research Opportunities,
  Research Runs, citations, and PDF upload flows.
- Topics, notifications, URL/text capture, answer synthesis, and standalone
  save-as-note remain explicitly marked as not connected; no mock records are
  presented as real data.
- No `.env`, migration, deployment configuration, Worker, or database logic was
  changed. No deployment or push was performed.
- PASS: `cd web && npm run typecheck`.
- PASS: `cd web && npm run build` (with the existing external package-lock/output
  tracing warning only).

## Library PDF deletion diagnosis (2026-10-22)

- BLOCKED BY REQUIRED SCHEMA/PERMISSION CHANGE: the V6 Library has no delete
  action, the existing upload DELETE route is limited to two-hour upload cleanup,
  and `asset-private` explicitly denies authenticated Storage DELETE operations.
- The Web runtime has no configured server-only Service Role client. Deleting the
  Asset first would also make its private Storage object inaccessible and orphaned.
- A protected owner-scoped deletion workflow must coordinate citation invalidation,
  Storage object removal, and Asset deletion before the UI can safely expose the
  action. No migration, permission, application code, or real data was changed.

## Asset deletion lifecycle implementation (2026-10-22)

- Added owner-scoped, idempotent `remove_original` and `purge_all` lifecycle
  requests with forced-RLS status records, immediate Retrieval/Publication
  fencing, active lease cancellation, citation invalidation, and Queue jobs.
- Added the Render Worker deletion capability. It uses the existing server-only
  Service Role gateway to recursively remove the exact Asset Storage prefix,
  then commits the fenced database cleanup; retries safely resume after partial
  Storage success.
- `remove_original` preserves the Asset, generated Notes/Claims, summaries, and
  Research history while removing the original PDF and disabling verification.
  `purge_all` removes source-owned revisions, artifacts, Evidence, source Notes,
  Claims, links, and Storage objects while preserving cross-source Research with
  invalidated snapshots for only the deleted source.
- Library and legacy Inbox now expose confirmation, pending/deleting/completed/
  failed states, retry entry points, and backup/audit retention disclosure.
- PASS: deletion Worker/Gateway/runner unit tests 9/9; PDF Inbox tests 2/2;
  TypeScript; Production Build; Python compile.
- BLOCKED: the new pgTAP protocol was not executed because local Supabase/Docker
  is unavailable. Linked dry-run made no changes but found pre-existing migration
  history drift: remote-only `20261021173539` and local-only `20261021145113`.
  Compare those migrations and repair history before applying
  `20261021193156_asset_deletion_lifecycle.sql`, then run the pgTAP protocol.
- No Cloud migration, real-data deletion, environment/deployment change, deploy,
  or push was performed.

## Supabase migration history drift reconciliation (2026-10-22)

- Confirmed Cloud migration `20261021173539_research_suggestion_engine` stores
  SQL byte-identical to the former local `20261021145113` file; the live tables,
  constraints, indexes, RLS policies, grants, triggers, and function bodies match.
- Renamed the local migration to `20261021173539_research_suggestion_engine.sql`
  so local history aligns with Cloud without repairing or mutating remote history.
- The later `20261021173759_fix_opportunity_enqueue_auth_user_access` remains
  aligned. Next action: deploy only `20261021193156_asset_deletion_lifecycle`.
## Manual Threads single-URL ingestion (2026-10-22)

- Added authenticated, tenant-scoped canonical Threads URL submission with atomic Asset +
  durable `ingest_threads` Job creation and per-user post-ID duplicate prevention.
- Added bounded Playwright public-post retrieval, explicit inaccessible/truncated outcomes,
  immutable normalized artifacts, post-offset Evidence, and lease-fenced Revision promotion.
- Reused the existing Source Note, retrieval, and Research pipelines; Threads citations retain
  the canonical source URL while PDF citations continue to use short-lived signed URLs.
- Connected the existing Add Data URL tab and Library polling/status UI; no bulk crawling,
  account monitoring, scheduled ingestion, login bypass, push, or deployment was added.
- Runtime prerequisite: install the pinned Worker dependencies and Playwright Chromium
  (`python -m playwright install chromium`) before enabling `WORKER_CAPABILITY=threads|all`.
- PASS: Threads Worker 4/4, Source Note 9/9, affected PDF/Research Worker 10/10,
  Threads Web 3/3, existing Web unit suites 10/10, TypeScript, targeted ESLint, production build.
- BLOCKED locally: Supabase pgTAP/API integration requires the unavailable Docker/OrbStack
  socket; `threads_ingestion_protocol.test.sql` and API duplicate coverage are ready to run.

### Threads citation provenance hardening (2026-10-22)

- Added a database trigger to make an established Threads Asset's `source_type`,
  `source_url`, and `source_external_id` immutable, including privileged writes.
- Threads Source Note, Research, and search citations now resolve the canonical URL from
  the Evidence/citation locator snapshot; PDF citations retain signed Storage URLs.
- Extended pgTAP coverage for identity immutability, immutable Evidence URLs, PDF behavior,
  and cross-tenant isolation. The SQL suite remains pending staging/local Supabase execution.
- PASS: Threads Web 4/4, Source Note Web 3/3, Research Web 3/3, Retrieval Web 3/3,
  affected Worker tests 19/19, TypeScript, and targeted ESLint.
- No Cloud database mutation, migration execution, Docker/OrbStack use, push, or deployment
  was performed.

## V6 Library Source Note integration (2026-10-22)

- Connected parsed PDF cards in `/library` to the existing authenticated
  `enqueue_source_note_generation` RPC and tenant-scoped Source Note loader.
- Added generating, completed, and retryable-failure states with automatic polling;
  completed notes render their body, Claims, Evidence quotes, and PDF page links inline.
- PASS: Source Note Web tests 3/3, TypeScript, and targeted ESLint.
- No Schema, Worker, LLM configuration, deployment, or push change was made.

## Vercel Threads module unblock (2026-10-22)

- Added the previously omitted tracked `web/src/lib/threads.ts` module and its URL provenance tests;
  verified PDF Inbox, Source Note, Research, Retrieval, TypeScript, and Web production build.
- Threads Cloud Migration, Chromium runtime, and Staging integration remain deployment blockers;
  no Cloud, Worker, Migration, commit, push, or deployment action was performed.
## R1 Unified Hybrid Retrieval MVP

- Added deterministic paragraph/sentence-aware chunking over the immutable full
  normalized PDF artifact. Chunks never cross page Evidence boundaries and retain
  Evidence, locator, absolute offsets, content hash, Revision, and Asset identity.
- Added a local-only additive pgvector migration with 1536-dimensional exact
  search, composite owner foreign keys plus a full provenance trigger, forced RLS,
  idempotent atomic chunk replacement, and active-ready-validated filtering.
- Added bounded dynamic OR-style `simple` FTS plus exact semantic candidates and
  RRF fusion. The result reports retrieval method and degraded semantic state;
  sufficiency is always `unassessed` and Top-K is not treated as full-document
  reading or proof that Evidence is sufficient.
- Added authenticated single/multi-Asset and whole-owned-KB scopes. The public RPC
  derives the user from Auth; the explicit user parameter is isolated in a
  service-role-only Worker RPC. Invalid, empty, foreign, and unavailable scopes
  fail closed without expanding search.
- Added an independent OpenAI-compatible embedding adapter using the documented
  multilingual `text-embedding-3-small` 1536-dimensional contract. Sol embedding
  support was not assumed. Configuration, bounded batches/timeouts/retries,
  response model/dimension validation, content-hash/model-version caching, and an
  incremental backfill command are separate from Source Note settings.
- PASS: Retrieval-specific Mock/unit tests 14/14; the non-network Worker regression
  suite 59/59 (including PDF ingestion/parser, Source Note, Research, deletion,
  runner, and gateway); Python compilation; and `git diff --check`.
- BLOCKED: seven unrelated Worker HTTP tests require a localhost socket that this
  sandbox denies. They reached no external service and no product data.
- NOT RUN: the 26-case pgTAP protocol suite because Docker/OrbStack and Cloud are
  excluded. NOT RUN: real embedding integration because no authorized provider
  credential was used. Neither Mock vectors nor unexecuted SQL are recorded as E2E.
- Existing `get_research_context`, PDF ingestion, Source Note, citations, Research
  synthesis, UI, Render settings, and Threads files were not changed.

### R1 Research Worker integration

- Research keeps the fenced `get_research_context` control-plane validation, then
  sends the user's unmodified question and optional validated Asset scope through
  the existing Hybrid Retriever and service-role-only Retrieval gateway.
- Hybrid RPC rows are explicitly converted to the Research model contract while
  retaining Evidence, Asset, Revision, locator, method, and degraded-state data.
  Existing draft synthesis and database citation revalidation remain unchanged.
- Embedding failure uses only an explicitly marked lexical fallback; a degraded
  response containing semantic/hybrid rows fails closed.
- PASS: targeted Research/Retrieval tests 25/25, non-network Worker regression
  65/65, Python compilation, and `git diff --check`. No Cloud request was made.
