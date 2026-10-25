# Production Research Staging, Backfill, and Rollback Runbook

## Current gate: BLOCKED

Do not run the deployment section against the currently linked project. It has
no isolated branch, no available backup, no PITR, and its remote migration
history disagrees with its schema (`retrieval_chunks` exists although R1 is not
recorded as applied).

## Accountless Local Staging (current safe path)

This path needs no additional Supabase account and cannot reach Production. It
is the approved environment for migration/RLS/HTTP rehearsal while the cloud
gate remains blocked.

```sh
cd /Users/yunchien/programming/knowledgeagent
open -a /Applications/OrbStack.app
SUPABASE_TELEMETRY_DISABLED=1 supabase start

# Destructive only to the disposable local database. Never add --linked.
SUPABASE_TELEMETRY_DISABLED=1 supabase db reset --local --no-seed
SUPABASE_TELEMETRY_DISABLED=1 supabase db lint --local
SUPABASE_TELEMETRY_DISABLED=1 supabase db advisors --local --type all \
  --level warn --fail-on error
SUPABASE_TELEMETRY_DISABLED=1 supabase test db
```

The verified local stack is loopback-only (`127.0.0.1:54321` API and
`127.0.0.1:54322` database). A clean reset applies the complete 17-migration
chain through `20261024185133_production_research_pipeline.sql` on PostgreSQL
17 and runs 279 pgTAP assertions.

For the full application E2E, first export reviewed embedding/Research-model
test credentials and start the production Web build. Use only a disposable local
Auth identity:

```sh
cd /Users/yunchien/programming/knowledgeagent
SUPABASE_TELEMETRY_DISABLED=1 supabase status -o json > /tmp/pb-local-status.json
chmod 600 /tmp/pb-local-status.json
export STAGING_MODE=local
export STAGING_REF=local-personal-brain
export PRODUCTION_REF=production-never-targeted
export STAGING_SUPABASE_URL="$(node -e 'const s=require("/tmp/pb-local-status.json");process.stdout.write(s.API_URL)')"
export STAGING_SERVICE_ROLE_KEY="$(node -e 'const s=require("/tmp/pb-local-status.json");process.stdout.write(s.SERVICE_ROLE_KEY)')"
export STAGING_WEB_URL=http://127.0.0.1:3000
export STAGING_TEST_EMAIL=local-e2e@example.test
export STAGING_TEST_PASSWORD='replace-with-a-local-test-password'
export STAGING_TEST_PDF='/absolute/path/to/citation-fixture.pdf'
export STAGING_RESEARCH_QUESTION='Question answered by the fixture'
export STAGING_ISOLATION_EVIDENCE=disposable-local-supabase
export ALLOW_STAGING_E2E=YES_I_HAVE_VERIFIED_LOCAL_ISOLATION

# Required reviewed test-provider settings; do not use Production credentials.
export EMBEDDING_API_BASE_URL='<test-provider-url>'
export EMBEDDING_API_KEY='<test-provider-key>'
export EMBEDDING_MODEL='<1536-dimension-model>'
export EMBEDDING_MODEL_VERSION='<reviewed-version>'
export LLM_API_BASE_URL='<test-provider-url>'
export LLM_API_KEY='<test-provider-key>'
export RESEARCH_MODEL='<reviewed-model>'

curl -fsS -X POST "$STAGING_SUPABASE_URL/auth/v1/admin/users" \
  -H "apikey: $STAGING_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $STAGING_SERVICE_ROLE_KEY" \
  -H 'content-type: application/json' \
  --data "{\"email\":\"$STAGING_TEST_EMAIL\",\"password\":\"$STAGING_TEST_PASSWORD\",\"email_confirm\":true}"

(cd web && npm run build && npm start -- --hostname 127.0.0.1 --port 3000) &
WEB_PID=$!
trap 'kill "$WEB_PID" 2>/dev/null || true; rm -f /tmp/pb-local-status.json' EXIT
cd web
node --experimental-strip-types tests/research-pipeline.staging.ts
```

Current limitation: Storage API v1.80.2 on the current local CLI generates a
three-part signed-upload token and then rejects it with `Invalid Compact JWS`.
This is reproduced by the official local stack and matches the open upstream
Storage issue <https://github.com/supabase/storage/issues/1268>. Do not bypass
the signed-upload check. Re-run the command after an upstream fix or after a
reviewed compatible CLI/Storage version is pinned; until then the full PDF E2E
is **BLOCKED**, while migration/RLS/Auth tests remain valid.

## Preflight

1. Confirm the target is the isolated Supabase Staging project and take a
   restorable backup/PITR checkpoint. Never use the Production ref for this run.
2. Verify `vector` and its version, Postgres version, available disk, lock
   budget, RLS/grants, current RPC signatures, and migration history.
3. Review `20261024185133_production_research_pipeline.sql`; verify the
   experimental CJK migration exists only under `docs/experimental/`.
4. Run TypeScript, Python, migration parse, and security tests from a clean
   checkout. Record commit SHA and environment versions.

## Staging deployment

```sh
export STAGING_REF='<isolated-staging-ref>'
export PRODUCTION_REF='<production-ref>'
test -n "$STAGING_REF" && test -n "$PRODUCTION_REF"
test "$STAGING_REF" != "$PRODUCTION_REF"

SUPABASE_TELEMETRY_DISABLED=1 supabase branches list \
  --project-ref "$PRODUCTION_REF" --output-format json | tee /tmp/pb-branches.json
SUPABASE_TELEMETRY_DISABLED=1 supabase backups list \
  --project-ref "$STAGING_REF" --output-format json | tee /tmp/pb-backups.json

# A human must verify that STAGING_REF is a separate persistent branch/project
# and that /tmp/pb-backups.json proves a usable restore point or PITR window.
SUPABASE_TELEMETRY_DISABLED=1 supabase link --project-ref "$STAGING_REF"
test "$(tr -d '\n' < supabase/.temp/project-ref)" = "$STAGING_REF"
SUPABASE_TELEMETRY_DISABLED=1 supabase migration list --linked
SUPABASE_TELEMETRY_DISABLED=1 supabase db push --linked --dry-run
SUPABASE_TELEMETRY_DISABLED=1 supabase db push --linked --skip-vault
SUPABASE_TELEMETRY_DISABLED=1 supabase db lint --linked \
  --schema public --level warning --fail-on error
SUPABASE_TELEMETRY_DISABLED=1 supabase db advisors --linked --type security
SUPABASE_TELEMETRY_DISABLED=1 supabase db advisors --linked --type performance
```

The HNSW and generated-search-column build can lock or consume substantial IO.
Schedule a Staging maintenance window and measure its duration before deciding
the Production window. New Data API exposure defaults require explicit grants;
this migration grants only the intended authenticated reads and service worker
writes.

## Backfill and workers

Deploy a dedicated worker with `WORKER_CAPABILITY=retrieval`. It seeds bounded
`index_retrieval` Jobs, claims them with `SKIP LOCKED`, heartbeats, retries with
backoff, validates model/dimension/version, and publishes chunks idempotently.
Keep `EMBEDDING_BATCH_SIZE`, provider timeout/retry, queue concurrency, and job
attempt limits bounded. The existing one-shot command remains available for a
small controlled repair:

```sh
cd worker
.venv/bin/python -m personal_brain.retrieval_backfill --page-size 10 --max-revisions 100
```

Monitor queue depth, attempt failures, embedding rate/cost, per-stage latency,
database connections, HNSW build/index size, and retrieval degraded status.

## E2E and performance

After the guarded deployment passes, run the full executable flow. All values
must identify disposable Staging resources; the command refuses to run without
the explicit isolation and backup acknowledgement.

```sh
cd web
export STAGING_REF='<isolated-staging-ref>'
export PRODUCTION_REF='<production-ref>'
export STAGING_SUPABASE_URL="https://${STAGING_REF}.supabase.co"
export STAGING_SERVICE_ROLE_KEY='<staging-service-role-key>'
export STAGING_WEB_URL='https://<staging-web-host>'
export STAGING_TEST_EMAIL='<dedicated-staging-test-user>'
export STAGING_TEST_PASSWORD='<dedicated-staging-test-password>'
export STAGING_TEST_PDF='/absolute/path/to/citation-fixture.pdf'
export STAGING_RESEARCH_QUESTION='<question answered verbatim by the fixture>'
export STAGING_BACKUP_EVIDENCE='<backup-id-or-PITR-window>'
export STAGING_ISOLATION_EVIDENCE='<branch-id-or-separate-project-change-ticket>'
export ALLOW_STAGING_E2E='YES_I_HAVE_VERIFIED_ISOLATION_AND_BACKUP'

# Also export the reviewed worker LLM and embedding settings documented in
# .env.example. This one command drives PDF -> ingestion -> indexing -> hybrid
# retrieval -> R2 verification -> citation -> Research Draft.
node --experimental-strip-types tests/research-pipeline.staging.ts
```

Then run RLS and database protocol tests against the same isolated Staging
clone using a transaction/rollback-capable CI runner:

```sh
SUPABASE_TELEMETRY_DISABLED=1 supabase test db
STAGING_STORAGE_TEST_MODE=allowlisted \
  python3 ../supabase/tests/storage_api_integration.py
```

Use two dedicated Staging users. Upload Chinese, English, and mixed PDFs; wait
for ingestion and indexing; run original and paraphrased questions; confirm
every citation resolves to the correct original page. Supersede/delete a source
and confirm it disappears from retrieval. Kill a retrieval and research worker
mid-stage, wait for lease expiry, and confirm exactly one idempotent publication.
Run the golden evaluation and performance protocol in `EVALUATION.md`.

## Roll-forward and rollback

Preferred recovery is roll-forward: pause Research/retrieval workers, correct
the function/index in a new migration, deploy to Staging, and resume. Existing
Evidence, revisions, notes, and citations remain untouched by the additive
schema.

For emergency rollback before Production traffic: pause workers, revoke the new
job-bound RPCs, restore prior RPC definitions from the immediately preceding
migrations, drop the new citation/retrieval triggers, and leave new columns and
tables in place until data is exported and audited. Dropping HNSW/GIN indexes is
safe but loses performance. Do not drop `research_run_evidence`, search columns,
or queued Jobs while they contain data. A destructive rollback requires a new
reviewed migration and verified backup; never edit deployed migration history.
