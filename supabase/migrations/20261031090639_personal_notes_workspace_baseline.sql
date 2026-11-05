-- Notes Workspace handoff baseline. This migration is additive and keeps the
-- existing public.notes row as the ownership, title, publication and optimistic
-- revision authority. Rich editor state and immutable import snapshots live in
-- dedicated tables so Source Notes and Research Notes remain unchanged.

create table public.personal_note_documents (
  note_id uuid primary key,
  user_id uuid not null,
  schema_version integer not null default 1 check (schema_version > 0),
  content_json jsonb not null default '{"type":"doc","content":[]}'::jsonb
    check (jsonb_typeof(content_json) = 'object'),
  plain_text text not null default '' check (char_length(plain_text) <= 1000000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint personal_note_documents_note_owner_fk
    foreign key (note_id, user_id) references public.notes (id, user_id)
    on delete cascade
);

create table public.personal_note_versions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  note_id uuid not null,
  body_revision integer not null check (body_revision > 0),
  title text not null check (char_length(title) between 1 and 300),
  content_json jsonb not null check (jsonb_typeof(content_json) = 'object'),
  plain_text text not null check (char_length(plain_text) <= 1000000),
  reason text not null check (reason in ('autosave','manual','import','restore')),
  created_at timestamptz not null default now(),
  unique (note_id, body_revision),
  constraint personal_note_versions_note_owner_fk
    foreign key (note_id, user_id) references public.notes (id, user_id)
    on delete cascade
);

create table public.personal_note_imports (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  note_id uuid not null,
  source_kind text not null check (
    source_kind in ('knowledge','threads','reels','research','docx','markdown','spreadsheet')
  ),
  source_note_id uuid,
  source_asset_id uuid,
  source_revision_id uuid,
  snapshot_title text not null check (char_length(snapshot_title) between 1 and 500),
  snapshot_text text not null check (char_length(snapshot_text) <= 1000000),
  snapshot_content_json jsonb not null default '{}'::jsonb
    check (jsonb_typeof(snapshot_content_json) = 'object'),
  imported_at timestamptz not null default now(),
  constraint personal_note_imports_id_user_note_unique unique (id, user_id, note_id),
  constraint personal_note_imports_note_owner_fk
    foreign key (note_id, user_id) references public.notes (id, user_id)
    on delete cascade
);

create table public.personal_note_citations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  note_id uuid not null,
  import_id uuid,
  evidence_id_snapshot uuid,
  asset_id_snapshot uuid,
  revision_id_snapshot uuid,
  locator_json jsonb not null default '{}'::jsonb
    check (jsonb_typeof(locator_json) = 'object'),
  supporting_quote text not null default '' check (char_length(supporting_quote) <= 20000),
  source_label text not null check (char_length(source_label) between 1 and 500),
  source_url text,
  validity_status text not null default 'valid'
    check (validity_status in ('valid','source_deleted','unavailable','user_modified')),
  created_at timestamptz not null default now(),
  constraint personal_note_citations_note_owner_fk
    foreign key (note_id, user_id) references public.notes (id, user_id)
    on delete cascade,
  constraint personal_note_citations_import_owner_fk
    foreign key (import_id, user_id, note_id)
    references public.personal_note_imports (id, user_id, note_id)
    on delete cascade
);

create function public.enforce_personal_note_manual_type()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if not exists (
    select 1
    from public.notes
    where id = new.note_id
      and user_id = new.user_id
      and note_type = 'manual_note'
  ) then
    raise check_violation using message = 'personal note data requires an owned manual_note';
  end if;
  return new;
end;
$$;

create trigger personal_note_documents_require_manual_note
before insert or update of note_id, user_id on public.personal_note_documents
for each row execute function public.enforce_personal_note_manual_type();
create trigger personal_note_versions_require_manual_note
before insert or update of note_id, user_id on public.personal_note_versions
for each row execute function public.enforce_personal_note_manual_type();
create trigger personal_note_imports_require_manual_note
before insert or update of note_id, user_id on public.personal_note_imports
for each row execute function public.enforce_personal_note_manual_type();
create trigger personal_note_citations_require_manual_note
before insert or update of note_id, user_id on public.personal_note_citations
for each row execute function public.enforce_personal_note_manual_type();

create index personal_note_documents_user_updated_idx
  on public.personal_note_documents (user_id, updated_at desc);
create index personal_note_versions_user_note_created_idx
  on public.personal_note_versions (user_id, note_id, created_at desc);
create index personal_note_imports_user_note_imported_idx
  on public.personal_note_imports (user_id, note_id, imported_at desc);
create index personal_note_citations_user_note_idx
  on public.personal_note_citations (user_id, note_id, created_at);

create trigger personal_note_documents_set_updated_at
before update on public.personal_note_documents
for each row execute function public.set_current_timestamp_updated_at();

alter table public.personal_note_documents enable row level security;
alter table public.personal_note_documents force row level security;
alter table public.personal_note_versions enable row level security;
alter table public.personal_note_versions force row level security;
alter table public.personal_note_imports enable row level security;
alter table public.personal_note_imports force row level security;
alter table public.personal_note_citations enable row level security;
alter table public.personal_note_citations force row level security;

create policy personal_note_documents_select_own
on public.personal_note_documents for select to authenticated
using ((select auth.uid()) = user_id);
create policy personal_note_documents_insert_own
on public.personal_note_documents for insert to authenticated
with check ((select auth.uid()) = user_id);
create policy personal_note_documents_update_own
on public.personal_note_documents for update to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);
create policy personal_note_documents_delete_own
on public.personal_note_documents for delete to authenticated
using ((select auth.uid()) = user_id);

create policy personal_note_versions_select_own
on public.personal_note_versions for select to authenticated
using ((select auth.uid()) = user_id);
create policy personal_note_versions_insert_own
on public.personal_note_versions for insert to authenticated
with check ((select auth.uid()) = user_id);

create policy personal_note_imports_select_own
on public.personal_note_imports for select to authenticated
using ((select auth.uid()) = user_id);
create policy personal_note_imports_insert_own
on public.personal_note_imports for insert to authenticated
with check ((select auth.uid()) = user_id);
create policy personal_note_imports_delete_own
on public.personal_note_imports for delete to authenticated
using ((select auth.uid()) = user_id);

create policy personal_note_citations_select_own
on public.personal_note_citations for select to authenticated
using ((select auth.uid()) = user_id);
create policy personal_note_citations_insert_own
on public.personal_note_citations for insert to authenticated
with check ((select auth.uid()) = user_id);
create policy personal_note_citations_update_own
on public.personal_note_citations for update to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);
create policy personal_note_citations_delete_own
on public.personal_note_citations for delete to authenticated
using ((select auth.uid()) = user_id);

revoke all on public.personal_note_documents, public.personal_note_versions,
  public.personal_note_imports, public.personal_note_citations from anon, authenticated;
revoke all on function public.enforce_personal_note_manual_type() from public;
grant select, insert, update, delete on public.personal_note_documents to authenticated;
grant select, insert on public.personal_note_versions to authenticated;
grant select, insert, delete on public.personal_note_imports to authenticated;
grant select, insert, update, delete on public.personal_note_citations to authenticated;

comment on table public.personal_note_documents is
  'Rich editor state for user-owned manual_note rows. API and editor implementation remain TODO.';
