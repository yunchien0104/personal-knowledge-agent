-- Authenticated users may remove only their own original upload. Derived objects
-- remain backend-managed, and ownership is checked against Auth, path, object
-- owner, and the owning Asset row.

drop policy if exists asset_objects_delete_denied on storage.objects;

create policy asset_originals_delete_own
on storage.objects
for delete
to authenticated
using (
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
