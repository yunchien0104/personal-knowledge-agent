# Personal Brain Migration Rollback

These migrations create user-owned content tables, RLS policies, and the private
`asset-private` Storage bucket. Rollback is destructive and must never be run
against a remote or production database without an approved backup and outage.

## Forward order

1. `20261020090000_create_content_foundation.sql`
2. `20261020090100_create_private_asset_storage.sql`
3. `20261020150127_allow_owned_original_storage_deletes.sql`
4. Run the pgTAP and Storage API integration tests before accepting the migrations.

## Risks and data-loss warning

- Dropping the tables permanently removes Asset metadata, revisions, evidence,
  Note bodies, and Note-to-Asset links.
- Deleting the bucket or its objects permanently removes uploaded PDFs and all
  normalized artifacts. Database rollback does not recover Storage bytes.
- Notes intentionally survive Asset deletion during normal product operation;
  this rollback is exceptional and drops both domains.
- The migration has no automatic down script to prevent accidental destructive
  rollback. Restore from a verified database backup and Storage copy when data
  preservation is required.

## Manual rollback order

1. Stop API/worker writes and create verified database and Storage backups.
2. Delete `asset-private` objects through the protected Storage service using a
   privileged server credential; do not expose that credential to a browser.
3. In one reviewed transaction, drop Storage policies in this order:
   `asset_originals_delete_own`, `asset_objects_update_denied`,
   `asset_originals_insert_own`, `asset_objects_select_own`.
4. Delete the `asset-private` row from `storage.buckets` only after it is empty.
5. Drop `public.note_assets`, `public.asset_evidence`, `public.asset_artifacts`.
6. Drop `assets_active_revision_owner_fk`, then drop
   `public.asset_revisions`, `public.assets`, and `public.notes`.
7. Drop `public.set_current_timestamp_updated_at()` after its triggers/tables
   are gone. Commit only after verifying no dependent objects remain.
8. To recover, reapply migrations in forward order and restore the database and
   Storage backups together so object keys and metadata remain consistent.

Never use `db reset`, destructive remote migration repair, or bucket deletion as
an unreviewed rollback shortcut.
