begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(45);

-- PostgreSQL only permits data-modifying CTEs at the top level. This
-- security-invoker helper executes one DML statement under the caller's current
-- role/JWT context and exposes ROW_COUNT to pgTAP without bypassing RLS.
create function pg_temp.affected_rows(statement text)
returns bigint
language plpgsql
as $$
declare
  affected bigint;
begin
  execute statement;
  get diagnostics affected = row_count;
  return affected;
end;
$$;

-- Two real rows in auth.users plus distinct authenticated JWT subjects. The test
-- never relies on a caller-supplied user_id without an Auth identity.
insert into auth.users (
  instance_id,
  id,
  aud,
  role,
  email,
  encrypted_password,
  email_confirmed_at,
  raw_app_meta_data,
  raw_user_meta_data,
  created_at,
  updated_at
)
values
  (
    '00000000-0000-0000-0000-000000000000',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    'authenticated',
    'authenticated',
    'phase1-a@example.test',
    '',
    now(),
    '{"provider":"email","providers":["email"]}'::jsonb,
    '{}'::jsonb,
    now(),
    now()
  ),
  (
    '00000000-0000-0000-0000-000000000000',
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    'authenticated',
    'authenticated',
    'phase1-b@example.test',
    '',
    now(),
    '{"provider":"email","providers":["email"]}'::jsonb,
    '{}'::jsonb,
    now(),
    now()
  );

select ok(
  (select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.assets'::regclass),
  'assets has forced RLS'
);
select ok(
  (select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.asset_revisions'::regclass),
  'asset_revisions has forced RLS'
);
select ok(
  (select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.asset_artifacts'::regclass),
  'asset_artifacts has forced RLS'
);
select ok(
  (select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.asset_evidence'::regclass),
  'asset_evidence has forced RLS'
);
select ok(
  (select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.notes'::regclass),
  'notes has forced RLS'
);
select ok(
  (select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.note_assets'::regclass),
  'note_assets has forced RLS'
);
select is(
  (select public from storage.buckets where id = 'asset-private'),
  false,
  'asset-private bucket is private'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'storage.objects'::regclass),
  'storage.objects has RLS enabled'
);

set local role authenticated;
select set_config(
  'request.jwt.claims',
  '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}',
  true
);

select lives_ok(
  $$insert into public.assets (id, user_id, source_type)
    values (
      '10000000-0000-4000-8000-000000000001',
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      'pdf'
    )$$,
  'user A can insert an owned asset'
);
select lives_ok(
  $$insert into public.assets (id, user_id, source_type)
    values (
      '10000000-0000-4000-8000-000000000009',
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      'threads'
    )$$,
  'user A can insert a disposable owned asset'
);
select lives_ok(
  $$insert into public.notes (id, user_id, note_type, title)
    values (
      '40000000-0000-4000-8000-000000000001',
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      'source_note',
      'A note'
    )$$,
  'user A can insert an owned note'
);
select lives_ok(
  $$insert into public.notes (id, user_id, note_type, title)
    values (
      '40000000-0000-4000-8000-000000000009',
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      'manual_note',
      'A persistent note'
    )$$,
  'user A can insert a note that must survive source deletion'
);
select lives_ok(
  $$insert into public.note_assets (user_id, note_id, asset_id)
    values (
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      '40000000-0000-4000-8000-000000000009',
      '10000000-0000-4000-8000-000000000009'
    )$$,
  'user A can link the persistent note to the disposable asset'
);
select throws_matching(
  $$insert into public.assets (user_id, source_type)
    values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'pdf')$$,
  'row-level security|permission denied',
  'user A cannot create an asset for user B'
);
select is(
  (select count(*) from public.assets where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
  2::bigint,
  'user A reads owned assets'
);

reset role;
set local role authenticated;
select set_config(
  'request.jwt.claims',
  '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}',
  true
);
select lives_ok(
  $$insert into public.assets (id, user_id, source_type)
    values (
      '20000000-0000-4000-8000-000000000002',
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      'pdf'
    )$$,
  'user B can insert an owned asset'
);
select lives_ok(
  $$insert into public.notes (id, user_id, note_type, title)
    values (
      '50000000-0000-4000-8000-000000000002',
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      'source_note',
      'B note'
    )$$,
  'user B can insert an owned note'
);

reset role;
insert into public.asset_revisions (
  id, user_id, asset_id, revision_no, fingerprint, processor_version, status
)
values
  (
    '30000000-0000-4000-8000-000000000001',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    '10000000-0000-4000-8000-000000000001',
    1,
    repeat('a', 64),
    'test-v1',
    'validated'
  ),
  (
    '30000000-0000-4000-8000-000000000002',
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    '20000000-0000-4000-8000-000000000002',
    1,
    repeat('b', 64),
    'test-v1',
    'validated'
  );
insert into public.asset_artifacts (
  id, user_id, revision_id, kind, object_key, sha256, mime, size_bytes, retention_class
)
values
  (
    '60000000-0000-4000-8000-000000000001',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    '30000000-0000-4000-8000-000000000001',
    'normalized',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/10000000-0000-4000-8000-000000000001/derived/a.json',
    repeat('c', 64),
    'application/json',
    10,
    'until_asset_deletion'
  ),
  (
    '60000000-0000-4000-8000-000000000002',
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    '30000000-0000-4000-8000-000000000002',
    'normalized',
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/20000000-0000-4000-8000-000000000002/derived/b.json',
    repeat('d', 64),
    'application/json',
    10,
    'until_asset_deletion'
  );
insert into public.asset_evidence (
  id, user_id, revision_id, locator_type, locator_json, excerpt, source_hash
)
values
  (
    '70000000-0000-4000-8000-000000000001',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    '30000000-0000-4000-8000-000000000001',
    'pdf',
    '{"page":1}',
    'A evidence',
    repeat('e', 64)
  ),
  (
    '70000000-0000-4000-8000-000000000002',
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    '30000000-0000-4000-8000-000000000002',
    'pdf',
    '{"page":2}',
    'B evidence',
    repeat('f', 64)
  );

set local role authenticated;
select set_config(
  'request.jwt.claims',
  '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}',
  true
);

select is(
  (select count(*) from public.assets where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'),
  0::bigint,
  'user A cannot read user B assets'
);
select is(
  pg_temp.affected_rows($sql$
    update public.assets set source_url = 'https://example.test/forbidden'
    where id = '20000000-0000-4000-8000-000000000002'
  $sql$),
  0::bigint,
  'user A cannot update user B assets'
);
select is(
  pg_temp.affected_rows($sql$
    delete from public.assets
    where id = '20000000-0000-4000-8000-000000000002'
  $sql$),
  0::bigint,
  'user A cannot delete user B assets'
);
select is(
  (select count(*) from public.asset_revisions),
  1::bigint,
  'user A only reads owned revisions'
);
select is(
  (select count(*) from public.asset_artifacts),
  1::bigint,
  'user A only reads owned artifacts'
);
select is(
  (select count(*) from public.asset_evidence),
  1::bigint,
  'user A only reads owned evidence'
);
select throws_matching(
  $$update public.assets set status = 'ready'
    where id = '10000000-0000-4000-8000-000000000001'$$,
  'permission denied',
  'authenticated users cannot change worker-managed asset status'
);
select throws_matching(
  $$update public.assets set active_revision_id = null
    where id = '10000000-0000-4000-8000-000000000001'$$,
  'permission denied',
  'authenticated users cannot promote asset revisions'
);
select throws_matching(
  $$insert into public.asset_revisions (
      user_id, asset_id, revision_no, fingerprint, processor_version
    ) values (
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      '10000000-0000-4000-8000-000000000001',
      2,
      repeat('9', 64),
      'browser-write'
    )$$,
  'permission denied|row-level security',
  'authenticated users cannot insert revisions'
);
select throws_matching(
  $$update public.asset_artifacts set kind = 'tampered'
    where id = '60000000-0000-4000-8000-000000000001'$$,
  'permission denied',
  'authenticated users cannot update artifacts'
);
select throws_matching(
  $$delete from public.asset_evidence
    where id = '70000000-0000-4000-8000-000000000001'$$,
  'permission denied',
  'authenticated users cannot delete evidence'
);
select lives_ok(
  $$insert into public.note_assets (user_id, note_id, asset_id)
    values (
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      '40000000-0000-4000-8000-000000000001',
      '10000000-0000-4000-8000-000000000001'
    )$$,
  'user A can link an owned note to an owned asset'
);
select throws_matching(
  $$insert into public.note_assets (user_id, note_id, asset_id)
    values (
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      '40000000-0000-4000-8000-000000000001',
      '20000000-0000-4000-8000-000000000002'
    )$$,
  'row-level security|foreign key',
  'user A cannot link an owned note to user B asset'
);
select is(
  pg_temp.affected_rows($sql$
    update public.assets set source_url = 'https://example.test/a'
    where id = '10000000-0000-4000-8000-000000000001'
  $sql$),
  1::bigint,
  'user A can update an owned asset'
);
select is(
  pg_temp.affected_rows($sql$
    delete from public.assets
    where id = '10000000-0000-4000-8000-000000000009'
  $sql$),
  1::bigint,
  'user A can delete an owned asset'
);
select is(
  (select count(*) from public.notes
   where id = '40000000-0000-4000-8000-000000000009'),
  1::bigint,
  'deleting an asset preserves the independent note'
);
select is(
  (select count(*) from public.note_assets
   where asset_id = '10000000-0000-4000-8000-000000000009'),
  0::bigint,
  'deleting an asset removes only its note link'
);

-- Storage authorization is exercised on storage.objects under two actual Auth
-- subjects, which is the same RLS layer used by the Storage API.
select lives_ok(
  $$insert into storage.objects (bucket_id, name, owner_id)
    values (
      'asset-private',
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/10000000-0000-4000-8000-000000000001/original/a.pdf',
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    )$$,
  'user A can upload an original under an owned asset path'
);
select throws_matching(
  $$insert into storage.objects (bucket_id, name, owner_id)
    values (
      'asset-private',
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/20000000-0000-4000-8000-000000000002/original/stolen.pdf',
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    )$$,
  'row-level security|permission denied',
  'user A cannot upload into user B path'
);

reset role;
set local role authenticated;
select set_config(
  'request.jwt.claims',
  '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}',
  true
);
select lives_ok(
  $$insert into storage.objects (bucket_id, name, owner_id)
    values (
      'asset-private',
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/20000000-0000-4000-8000-000000000002/original/b.pdf',
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    )$$,
  'user B can upload an original under an owned asset path'
);

reset role;
set local role authenticated;
select set_config(
  'request.jwt.claims',
  '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}',
  true
);
select is(
  (select count(*) from storage.objects
    where name like 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/%'),
  0::bigint,
  'user A cannot list or read user B storage objects'
);
select is(
  pg_temp.affected_rows($sql$
    update storage.objects set metadata = '{"tampered":true}'
    where name like 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/%'
  $sql$),
  0::bigint,
  'user A cannot update user B storage objects'
);
select throws_matching(
  $$delete from storage.objects
    where name like 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/%'$$,
  'Direct deletion from storage tables is not allowed',
  'user A cannot delete user B storage objects'
);
select is(
  pg_temp.affected_rows($sql$
    update storage.objects set metadata = '{"replaced":true}'
    where name like 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/%'
  $sql$),
  0::bigint,
  'authenticated users cannot replace uploaded objects directly'
);
select throws_matching(
  $$delete from storage.objects
    where name like 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/%'$$,
  'Direct deletion from storage tables is not allowed',
  'authenticated users cannot delete storage objects directly'
);

reset role;
select throws_matching(
  $$insert into public.note_assets (user_id, note_id, asset_id)
    values (
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      '40000000-0000-4000-8000-000000000001',
      '20000000-0000-4000-8000-000000000002'
    )$$,
  'foreign key constraint',
  'composite foreign keys reject cross-user relations even for privileged writes'
);
select is(
  (select status from public.assets where id = '20000000-0000-4000-8000-000000000002'),
  'pending',
  'user B asset was not modified by user A'
);
select is(
  (select count(*) from storage.objects
   where name like 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/%'),
  1::bigint,
  'user B object was not modified or deleted by user A'
);

select * from finish();
rollback;
