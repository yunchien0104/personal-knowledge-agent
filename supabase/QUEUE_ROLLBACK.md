# Step 4A Queue-only Rollback

This rollback removes only migration
`20261021034022_durable_pdf_ingestion_queue.sql`. It must not delete or alter
Assets, Auth users, Storage buckets, Storage objects, revisions, or evidence.

## Dependencies and prerequisites

- `jobs` references `auth.users` and `public.assets`; `job_attempts` references
  `jobs`. The four Queue RPCs depend on these tables, and `claim_job` returns the
  `jobs` row type.
- Stop all workers and disable or roll back the Web code that calls
  `enqueue_pdf_ingestion`. Confirm no active lease can continue writing.
- Export `jobs` and `job_attempts` if queue history, retry diagnostics, or pending
  work must be retained. Verify the backup before proceeding.
- For Staging or Production, use a reviewed forward rollback migration. Do not
  rewrite Supabase migration history or run `db reset`.

## Data-loss warning

Dropping these tables permanently removes every pending/running Job, lease,
fencing generation, attempt, retry schedule, and safe error record. It does not
remove the referenced Asset, Auth identity, or private Storage object.

## Safe execution order

Run the following in one reviewed transaction after the prerequisites are met:

1. Revoke and drop `public.enqueue_pdf_ingestion(uuid)`.
2. Revoke and drop `public.claim_job(text, text, integer)`.
3. Revoke and drop `public.heartbeat_job(uuid, text, bigint, integer)`.
4. Revoke and drop
   `public.report_job_failure(uuid, text, bigint, text, text, text, boolean)`.
5. Drop `public.job_attempts`.
6. Drop `public.jobs`; its `jobs_set_updated_at` trigger is removed with the table.
7. Verify that `public.assets`, `auth.users`, `storage.buckets`, and
   `storage.objects` were not modified, then commit.

Do not use `CASCADE`: an unexpected dependency must stop the rollback for review.
To recover, reapply the Queue migration in forward order and restore the queue
backup only if its Asset/Auth foreign-key targets still exist.
