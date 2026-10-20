-- Private source/artifact storage. Object names must use:
--   <user_id>/<asset_id>/original/<immutable-file-name>
-- Worker-created derived objects use the same first two segments under a
-- non-original third segment. Authenticated users can read, but never mutate,
-- derived objects.

insert into storage.buckets (
  id,
  name,
  public,
  file_size_limit,
  allowed_mime_types
)
values (
  'asset-private',
  'asset-private',
  false,
  52428800,
  array['application/pdf', 'application/json', 'text/plain', 'image/jpeg', 'image/png']
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

create policy asset_objects_select_own
on storage.objects
for select
to authenticated
using (
  bucket_id = 'asset-private'
  and (storage.foldername(name))[1] = (select auth.uid())::text
  and exists (
    select 1
    from public.assets
    where assets.user_id = (select auth.uid())
      and assets.id::text = (storage.foldername(name))[2]
  )
);

create policy asset_originals_insert_own
on storage.objects
for insert
to authenticated
with check (
  bucket_id = 'asset-private'
  and owner_id = (select auth.uid())::text
  and (storage.foldername(name))[1] = (select auth.uid())::text
  and (storage.foldername(name))[3] = 'original'
  and exists (
    select 1
    from public.assets
    where assets.user_id = (select auth.uid())
      and assets.id::text = (storage.foldername(name))[2]
  )
);

-- Source deletion and replacement must go through a protected backend workflow
-- so DB tombstones/citation invalidation and Storage cleanup remain coordinated.
create policy asset_objects_update_denied
on storage.objects
for update
to authenticated
using (false)
with check (false);

create policy asset_objects_delete_denied
on storage.objects
for delete
to authenticated
using (false);
