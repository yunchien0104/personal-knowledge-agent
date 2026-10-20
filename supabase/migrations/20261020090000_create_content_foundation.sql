-- Phase 1 / Task 2: user-owned source and note foundation.
-- Derived source rows are readable by their owner but writable only by privileged
-- backend roles that bypass RLS. All ownership joins are enforced in the database.

create table public.assets (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  source_type text not null
    constraint assets_source_type_check
    check (source_type in ('pdf', 'instagram_reel', 'threads')),
  source_url text,
  source_external_id text,
  status text not null default 'pending'
    constraint assets_status_check
    check (status in ('pending', 'processing', 'ready', 'failed', 'unsupported')),
  active_revision_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint assets_id_user_id_unique unique (id, user_id)
);

create unique index assets_user_source_external_unique
  on public.assets (user_id, source_type, source_external_id)
  where source_external_id is not null;
create index assets_user_status_created_idx
  on public.assets (user_id, status, created_at desc);

create table public.asset_revisions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  asset_id uuid not null,
  revision_no integer not null constraint asset_revisions_revision_no_check check (revision_no > 0),
  fingerprint text not null,
  processor_version text not null,
  status text not null default 'staged'
    constraint asset_revisions_status_check
    check (status in ('staged', 'processing', 'validated', 'failed')),
  normalized_object_key text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint asset_revisions_id_user_id_unique unique (id, user_id),
  constraint asset_revisions_asset_revision_unique unique (asset_id, revision_no),
  constraint asset_revisions_processor_unique
    unique (asset_id, fingerprint, processor_version),
  constraint asset_revisions_object_key_owner_check
    check (
      normalized_object_key is null
      or normalized_object_key like user_id::text || '/' || asset_id::text || '/%'
    ),
  constraint asset_revisions_asset_owner_fk
    foreign key (asset_id, user_id)
    references public.assets (id, user_id)
    on delete cascade
);

create index asset_revisions_user_asset_created_idx
  on public.asset_revisions (user_id, asset_id, created_at desc);

alter table public.assets
  add constraint assets_active_revision_owner_fk
  foreign key (active_revision_id, user_id)
  references public.asset_revisions (id, user_id)
  deferrable initially deferred;

create table public.asset_artifacts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  revision_id uuid not null,
  kind text not null,
  object_key text not null,
  sha256 text not null
    constraint asset_artifacts_sha256_check
    check (sha256 ~ '^[0-9a-f]{64}$'),
  mime text not null,
  size_bytes bigint not null constraint asset_artifacts_size_check check (size_bytes >= 0),
  retention_class text not null,
  created_at timestamptz not null default now(),
  constraint asset_artifacts_object_key_unique unique (object_key),
  constraint asset_artifacts_object_key_owner_check
    check (object_key like user_id::text || '/%'),
  constraint asset_artifacts_revision_owner_fk
    foreign key (revision_id, user_id)
    references public.asset_revisions (id, user_id)
    on delete cascade
);

create index asset_artifacts_user_revision_kind_idx
  on public.asset_artifacts (user_id, revision_id, kind);

create table public.asset_evidence (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  revision_id uuid not null,
  locator_type text not null
    constraint asset_evidence_locator_type_check
    check (locator_type in ('pdf', 'video', 'post')),
  locator_json jsonb not null
    constraint asset_evidence_locator_object_check
    check (jsonb_typeof(locator_json) = 'object'),
  excerpt text not null,
  source_hash text not null
    constraint asset_evidence_source_hash_check
    check (source_hash ~ '^[0-9a-f]{64}$'),
  content_object_key text,
  created_at timestamptz not null default now(),
  constraint asset_evidence_object_key_owner_check
    check (
      content_object_key is null
      or content_object_key like user_id::text || '/%'
    ),
  constraint asset_evidence_revision_owner_fk
    foreign key (revision_id, user_id)
    references public.asset_revisions (id, user_id)
    on delete cascade
);

create index asset_evidence_user_revision_locator_idx
  on public.asset_evidence (user_id, revision_id, locator_type);

create table public.notes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  note_type text not null
    constraint notes_note_type_check
    check (note_type in ('source_note', 'research_note', 'manual_note')),
  title text not null,
  body text not null default '',
  body_revision integer not null default 1
    constraint notes_body_revision_check check (body_revision > 0),
  publication_state text not null default 'draft'
    constraint notes_publication_state_check
    check (publication_state in ('draft', 'published', 'archived')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint notes_id_user_id_unique unique (id, user_id)
);

create index notes_user_updated_idx on public.notes (user_id, updated_at desc);
create index notes_user_type_state_idx
  on public.notes (user_id, note_type, publication_state);

create table public.note_assets (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  note_id uuid not null,
  asset_id uuid not null,
  relationship text not null default 'source'
    constraint note_assets_relationship_check
    check (relationship in ('source', 'context', 'derived_from')),
  created_at timestamptz not null default now(),
  constraint note_assets_note_asset_unique unique (note_id, asset_id),
  constraint note_assets_note_owner_fk
    foreign key (note_id, user_id)
    references public.notes (id, user_id)
    on delete cascade,
  constraint note_assets_asset_owner_fk
    foreign key (asset_id, user_id)
    references public.assets (id, user_id)
    on delete cascade
);

create index note_assets_user_asset_idx on public.note_assets (user_id, asset_id);

create function public.set_current_timestamp_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

revoke all on function public.set_current_timestamp_updated_at() from public;

create trigger assets_set_updated_at
before update on public.assets
for each row execute function public.set_current_timestamp_updated_at();

create trigger asset_revisions_set_updated_at
before update on public.asset_revisions
for each row execute function public.set_current_timestamp_updated_at();

create trigger notes_set_updated_at
before update on public.notes
for each row execute function public.set_current_timestamp_updated_at();

alter table public.assets enable row level security;
alter table public.assets force row level security;
alter table public.asset_revisions enable row level security;
alter table public.asset_revisions force row level security;
alter table public.asset_artifacts enable row level security;
alter table public.asset_artifacts force row level security;
alter table public.asset_evidence enable row level security;
alter table public.asset_evidence force row level security;
alter table public.notes enable row level security;
alter table public.notes force row level security;
alter table public.note_assets enable row level security;
alter table public.note_assets force row level security;

create policy assets_select_own on public.assets
for select to authenticated
using ((select auth.uid()) = user_id);
create policy assets_insert_own on public.assets
for insert to authenticated
with check ((select auth.uid()) = user_id);
create policy assets_update_own on public.assets
for update to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);
create policy assets_delete_own on public.assets
for delete to authenticated
using ((select auth.uid()) = user_id);

create policy asset_revisions_select_own on public.asset_revisions
for select to authenticated
using ((select auth.uid()) = user_id);
create policy asset_artifacts_select_own on public.asset_artifacts
for select to authenticated
using ((select auth.uid()) = user_id);
create policy asset_evidence_select_own on public.asset_evidence
for select to authenticated
using ((select auth.uid()) = user_id);

create policy notes_select_own on public.notes
for select to authenticated
using ((select auth.uid()) = user_id);
create policy notes_insert_own on public.notes
for insert to authenticated
with check ((select auth.uid()) = user_id);
create policy notes_update_own on public.notes
for update to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);
create policy notes_delete_own on public.notes
for delete to authenticated
using ((select auth.uid()) = user_id);

create policy note_assets_select_own on public.note_assets
for select to authenticated
using ((select auth.uid()) = user_id);
create policy note_assets_insert_own on public.note_assets
for insert to authenticated
with check ((select auth.uid()) = user_id);
create policy note_assets_update_own on public.note_assets
for update to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);
create policy note_assets_delete_own on public.note_assets
for delete to authenticated
using ((select auth.uid()) = user_id);

revoke all on public.assets from anon, authenticated;
revoke all on public.asset_revisions from anon, authenticated;
revoke all on public.asset_artifacts from anon, authenticated;
revoke all on public.asset_evidence from anon, authenticated;
revoke all on public.notes from anon, authenticated;
revoke all on public.note_assets from anon, authenticated;

grant usage on schema public to authenticated;
grant select, delete on public.assets to authenticated;
grant insert (id, user_id, source_type, source_url, source_external_id)
  on public.assets to authenticated;
grant update (source_url, source_external_id) on public.assets to authenticated;
grant select on public.asset_revisions to authenticated;
grant select on public.asset_artifacts to authenticated;
grant select on public.asset_evidence to authenticated;
grant select, insert, update, delete on public.notes to authenticated;
grant select, insert, update, delete on public.note_assets to authenticated;

grant select, insert, update, delete on public.assets to service_role;
grant select, insert, update, delete on public.asset_revisions to service_role;
grant select, insert, update, delete on public.asset_artifacts to service_role;
grant select, insert, update, delete on public.asset_evidence to service_role;
grant select, insert, update, delete on public.notes to service_role;
grant select, insert, update, delete on public.note_assets to service_role;
