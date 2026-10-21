begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(25);

select ok(
  not has_function_privilege(
    'anon',
    'public.publish_pdf_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb)',
    'execute'
  ),
  'anon cannot publish PDF ingestion results'
);
select ok(
  not has_function_privilege(
    'authenticated',
    'public.publish_pdf_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb)',
    'execute'
  ),
  'authenticated cannot publish PDF ingestion results'
);
select ok(
  has_function_privilege(
    'service_role',
    'public.publish_pdf_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb)',
    'execute'
  ),
  'service_role can publish PDF ingestion results'
);
select ok(
  (
    select count(*) = 1
      and count(*) filter (where coalesce(granted_role.rolname, 'PUBLIC') = 'service_role') = 1
    from pg_proc as function
    cross join lateral aclexplode(
      coalesce(function.proacl, acldefault('f', function.proowner))
    ) as privilege
    left join pg_roles as granted_role on granted_role.oid = privilege.grantee
    where function.oid = 'public.publish_pdf_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb)'::regprocedure
      and privilege.privilege_type = 'EXECUTE'
      and privilege.grantee <> function.proowner
  ),
  'publication RPC has one explicit non-owner grant'
);

set local role anon;
select throws_matching(
  $$select public.publish_pdf_ingestion(
    gen_random_uuid(), 'forbidden', 1, repeat('a', 64), 'native-text-pypdf-v1',
    'forbidden', repeat('b', 64), 1, '[]'::jsonb
  )$$,
  'permission denied for function publish_pdf_ingestion',
  'anon invocation is denied'
);
reset role;
set local role authenticated;
select throws_matching(
  $$select public.publish_pdf_ingestion(
    gen_random_uuid(), 'forbidden', 1, repeat('a', 64), 'native-text-pypdf-v1',
    'forbidden', repeat('b', 64), 1, '[]'::jsonb
  )$$,
  'permission denied for function publish_pdf_ingestion',
  'authenticated invocation is denied'
);
reset role;

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values (
  '00000000-0000-0000-0000-000000000000',
  'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  'authenticated', 'authenticated', 'publication@example.test', '', now(),
  '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now()
);

insert into public.assets (id, user_id, source_type) values
  ('41000000-0000-4000-8000-000000000001', 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'pdf'),
  ('41000000-0000-4000-8000-000000000002', 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'pdf'),
  ('41000000-0000-4000-8000-000000000003', 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'pdf');

insert into public.jobs (
  id, user_id, asset_id, job_type, payload_json, idempotency_key, priority
) values
  (
    '42000000-0000-4000-8000-000000000001',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    '41000000-0000-4000-8000-000000000001', 'ingest_pdf',
    '{"schemaVersion":1,"jobId":"42000000-0000-4000-8000-000000000001","userId":"dddddddd-dddd-4ddd-8ddd-dddddddddddd","jobType":"ingest_pdf","idempotencyKey":"publish-success","input":{"assetId":"41000000-0000-4000-8000-000000000001"}}'::jsonb,
    'publish-success', 300
  ),
  (
    '42000000-0000-4000-8000-000000000002',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    '41000000-0000-4000-8000-000000000002', 'ingest_pdf',
    '{"schemaVersion":1,"jobId":"42000000-0000-4000-8000-000000000002","userId":"dddddddd-dddd-4ddd-8ddd-dddddddddddd","jobType":"ingest_pdf","idempotencyKey":"publish-expired","input":{"assetId":"41000000-0000-4000-8000-000000000002"}}'::jsonb,
    'publish-expired', 200
  ),
  (
    '42000000-0000-4000-8000-000000000003',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    '41000000-0000-4000-8000-000000000003', 'ingest_pdf',
    '{"schemaVersion":1,"jobId":"42000000-0000-4000-8000-000000000003","userId":"dddddddd-dddd-4ddd-8ddd-dddddddddddd","jobType":"ingest_pdf","idempotencyKey":"publish-retry","input":{"assetId":"41000000-0000-4000-8000-000000000003"}}'::jsonb,
    'publish-retry', 100
  );

create temporary table publication_state (
  revision_id uuid,
  pages jsonb not null,
  object_key text not null
);
insert into publication_state (pages, object_key) values (
  jsonb_build_array(
    jsonb_build_object(
      'page_number', 1, 'text', 'Page one', 'start_offset', 0, 'end_offset', 8,
      'source_hash', repeat('a', 64),
      'evidence_locator', jsonb_build_object(
        'kind', 'pdf_page', 'page_number', 1, 'start_offset', 0,
        'end_offset', 8, 'source_hash', repeat('a', 64)
      )
    ),
    jsonb_build_object(
      'page_number', 2, 'text', 'Page two', 'start_offset', 10, 'end_offset', 18,
      'source_hash', repeat('a', 64),
      'evidence_locator', jsonb_build_object(
        'kind', 'pdf_page', 'page_number', 2, 'start_offset', 10,
        'end_offset', 18, 'source_hash', repeat('a', 64)
      )
    )
  ),
  'dddddddd-dddd-4ddd-8ddd-dddddddddddd/41000000-0000-4000-8000-000000000001/revisions/'
    || repeat('a', 64) || '/native-text-pypdf-v1/native-text.json'
);
grant select, update on publication_state to service_role;

set local role service_role;
select is(
  (select status from public.claim_job('publication-worker', 'lightweight', 120)),
  'running',
  'worker claims the publication job'
);
select is(
  public.heartbeat_job(
    '42000000-0000-4000-8000-000000000001', 'publication-worker', 1, 120
  ),
  true,
  'active worker heartbeat succeeds'
);
update publication_state
set revision_id = (
  public.publish_pdf_ingestion(
    '42000000-0000-4000-8000-000000000001', 'publication-worker', 1,
    repeat('a', 64), 'native-text-pypdf-v1', object_key,
    repeat('b', 64), 512, pages
  ) ->> 'revisionId'
)::uuid;
select is(
  (select status from public.jobs where id = '42000000-0000-4000-8000-000000000001'),
  'completed',
  'publication completes the job'
);
select is(
  (select status from public.assets where id = '41000000-0000-4000-8000-000000000001'),
  'ready',
  'publication makes the Asset ready'
);
select is(
  (select active_revision_id from public.assets where id = '41000000-0000-4000-8000-000000000001'),
  (select revision_id from publication_state),
  'Asset points at the immutable revision'
);
select is(
  (select count(*) from public.asset_artifacts where revision_id = (select revision_id from publication_state)),
  1::bigint,
  'publication creates one normalized Artifact'
);
select is(
  (select count(*) from public.asset_evidence where revision_id = (select revision_id from publication_state)),
  2::bigint,
  'publication creates one Evidence row per PDF page'
);
select results_eq(
  $$select locator_json ->> 'kind', (locator_json ->> 'page_number')::integer
    from public.asset_evidence
    where revision_id = (select revision_id from publication_state)
    order by (locator_json ->> 'page_number')::integer$$,
  $$values ('pdf_page', 1), ('pdf_page', 2)$$,
  'Evidence locators retain page numbers'
);
select is(
  (public.publish_pdf_ingestion(
    '42000000-0000-4000-8000-000000000001', 'publication-worker', 1,
    repeat('a', 64), 'native-text-pypdf-v1',
    (select object_key from publication_state), repeat('b', 64), 512,
    (select pages from publication_state)
  ) ->> 'revisionId')::uuid,
  (select revision_id from publication_state),
  'completed publication replay returns the same Revision'
);
select is(
  (select count(*) from public.asset_revisions where asset_id = '41000000-0000-4000-8000-000000000001'),
  1::bigint,
  'completed replay creates no duplicate Revision'
);

select is(
  (select status from public.claim_job('expired-worker', 'lightweight', 120)),
  'running',
  'second job is claimed for stale lease coverage'
);
update public.jobs set lease_expires_at = now() - interval '1 second'
where id = '42000000-0000-4000-8000-000000000002';
select is(
  public.publish_pdf_ingestion(
    '42000000-0000-4000-8000-000000000002', 'expired-worker', 1,
    repeat('a', 64), 'native-text-pypdf-v1',
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd/41000000-0000-4000-8000-000000000002/revisions/'
      || repeat('a', 64) || '/native-text-pypdf-v1/native-text.json',
    repeat('b', 64), 512, (select pages from publication_state)
  ),
  null::jsonb,
  'expired lease cannot publish'
);
select is(
  (select count(*) from public.asset_revisions where asset_id = '41000000-0000-4000-8000-000000000002'),
  0::bigint,
  'expired publication creates no Revision'
);
update public.jobs
set status = 'failed', lease_owner = null, lease_expires_at = null
where id = '42000000-0000-4000-8000-000000000002';

insert into public.jobs (
  id, user_id, asset_id, job_type, payload_json, idempotency_key, priority
) values (
  '42000000-0000-4000-8000-000000000004',
  'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  '41000000-0000-4000-8000-000000000001', 'ingest_pdf',
  '{"schemaVersion":1,"jobId":"42000000-0000-4000-8000-000000000004","userId":"dddddddd-dddd-4ddd-8ddd-dddddddddddd","jobType":"ingest_pdf","idempotencyKey":"publish-later-failure","input":{"assetId":"41000000-0000-4000-8000-000000000001"}}'::jsonb,
  'publish-later-failure', 250
);
select is(
  (select status from public.claim_job('failure-worker', 'lightweight', 120)),
  'running',
  'later processing attempt is claimed'
);
select is(
  public.report_job_failure(
    '42000000-0000-4000-8000-000000000004', 'failure-worker', 1,
    'permanent', 'corrupt_pdf', 'PDF is corrupt or unreadable', false
  ),
  'failed',
  'permanent Worker failure is recorded'
);
select ok(
  (select status = 'ready' and active_revision_id = (select revision_id from publication_state)
   from public.assets where id = '41000000-0000-4000-8000-000000000001'),
  'later failure preserves the existing successful Revision'
);

select is(
  (select status from public.claim_job('retry-worker', 'lightweight', 120)),
  'running',
  'transient failure job is claimed'
);
select is(
  public.report_job_failure(
    '42000000-0000-4000-8000-000000000003', 'retry-worker', 1,
    'transient', 'storage_unavailable', 'Storage is temporarily unavailable', true
  ),
  'retry_wait',
  'transient Worker failure schedules a retry'
);
select is(
  (select count(*) from public.asset_revisions where asset_id = '41000000-0000-4000-8000-000000000003'),
  0::bigint,
  'retryable failure publishes no derived rows'
);

reset role;
select * from finish();
rollback;
