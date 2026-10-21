begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(44);

select ok(
  (select relrowsecurity and relforcerowsecurity
   from pg_class where oid = 'public.jobs'::regclass),
  'jobs has forced RLS'
);
select ok(
  (select relrowsecurity and relforcerowsecurity
   from pg_class where oid = 'public.job_attempts'::regclass),
  'job_attempts has forced RLS'
);
select ok(
  not has_table_privilege('authenticated', 'public.jobs', 'insert'),
  'authenticated users cannot insert arbitrary jobs'
);
select ok(
  has_function_privilege('authenticated', 'public.enqueue_pdf_ingestion(uuid)', 'execute'),
  'authenticated users may call the ownership-checked enqueue function'
);
select ok(
  not has_function_privilege('anon', 'public.claim_job(text,text,integer)', 'execute'),
  'anon cannot execute claim_job'
);
select ok(
  not has_function_privilege('anon', 'public.heartbeat_job(uuid,text,bigint,integer)', 'execute'),
  'anon cannot execute heartbeat_job'
);
select ok(
  not has_function_privilege(
    'anon',
    'public.report_job_failure(uuid,text,bigint,text,text,text,boolean)',
    'execute'
  ),
  'anon cannot execute report_job_failure'
);
select ok(
  not has_function_privilege('authenticated', 'public.claim_job(text,text,integer)', 'execute'),
  'authenticated cannot execute claim_job'
);
select ok(
  not has_function_privilege(
    'authenticated',
    'public.heartbeat_job(uuid,text,bigint,integer)',
    'execute'
  ),
  'authenticated cannot execute heartbeat_job'
);
select ok(
  not has_function_privilege(
    'authenticated',
    'public.report_job_failure(uuid,text,bigint,text,text,text,boolean)',
    'execute'
  ),
  'authenticated cannot execute report_job_failure'
);
select ok(
  has_function_privilege('service_role', 'public.claim_job(text,text,integer)', 'execute')
  and has_function_privilege(
    'service_role',
    'public.heartbeat_job(uuid,text,bigint,integer)',
    'execute'
  )
  and has_function_privilege(
    'service_role',
    'public.report_job_failure(uuid,text,bigint,text,text,text,boolean)',
    'execute'
  ),
  'service_role can execute every worker RPC'
);
select ok(
  (
    with worker_functions(oid) as (
      values
        ('public.claim_job(text,text,integer)'::regprocedure::oid),
        ('public.heartbeat_job(uuid,text,bigint,integer)'::regprocedure::oid),
        ('public.report_job_failure(uuid,text,bigint,text,text,text,boolean)'::regprocedure::oid)
    ), worker_grants as (
      select function.oid as function_oid,
             coalesce(granted_role.rolname, 'PUBLIC') as grantee
      from worker_functions
      join pg_proc as function on function.oid = worker_functions.oid
      cross join lateral aclexplode(
        coalesce(function.proacl, acldefault('f', function.proowner))
      ) as privilege
      left join pg_roles as granted_role on granted_role.oid = privilege.grantee
      where privilege.privilege_type = 'EXECUTE'
        and privilege.grantee <> function.proowner
    )
    select count(*) = 3
      and count(*) filter (where grantee = 'service_role') = 3
    from worker_grants
  ),
  'each worker RPC grants EXECUTE exactly to service_role and no other non-owner role'
);

set local role anon;
select throws_matching(
  $$select * from public.claim_job('forbidden-anon', 'lightweight', 120)$$,
  'permission denied for function claim_job',
  'anon cannot invoke claim_job'
);
select throws_matching(
  $$select public.heartbeat_job('00000000-0000-0000-0000-000000000000', 'forbidden-anon', 1, 120)$$,
  'permission denied for function heartbeat_job',
  'anon cannot invoke heartbeat_job'
);
select throws_matching(
  $$select public.report_job_failure('00000000-0000-0000-0000-000000000000', 'forbidden-anon', 1, 'denied', 'denied', null, false)$$,
  'permission denied for function report_job_failure',
  'anon cannot invoke report_job_failure'
);

reset role;
set local role authenticated;
select throws_matching(
  $$select * from public.claim_job('forbidden-authenticated', 'lightweight', 120)$$,
  'permission denied for function claim_job',
  'authenticated cannot invoke claim_job'
);
select throws_matching(
  $$select public.heartbeat_job('00000000-0000-0000-0000-000000000000', 'forbidden-authenticated', 1, 120)$$,
  'permission denied for function heartbeat_job',
  'authenticated cannot invoke heartbeat_job'
);
select throws_matching(
  $$select public.report_job_failure('00000000-0000-0000-0000-000000000000', 'forbidden-authenticated', 1, 'denied', 'denied', null, false)$$,
  'permission denied for function report_job_failure',
  'authenticated cannot invoke report_job_failure'
);
reset role;

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values
  (
    '00000000-0000-0000-0000-000000000000',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    'authenticated', 'authenticated', 'queue-a@example.test', '', now(),
    '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now()
  ),
  (
    '00000000-0000-0000-0000-000000000000',
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    'authenticated', 'authenticated', 'queue-b@example.test', '', now(),
    '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now()
  );

insert into public.assets (id, user_id, source_type) values
  (
    '10000000-0000-4000-8000-000000000001',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    'pdf'
  ),
  (
    '20000000-0000-4000-8000-000000000002',
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    'pdf'
  );
insert into storage.objects (bucket_id, name, owner_id) values
  (
    'asset-private',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/10000000-0000-4000-8000-000000000001/original/source.pdf',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  ),
  (
    'asset-private',
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/20000000-0000-4000-8000-000000000002/original/source.pdf',
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
  );

create temporary table queue_test_state (job_id uuid not null);
grant select, insert on queue_test_state to authenticated, service_role;

set local role authenticated;
select set_config(
  'request.jwt.claims',
  '{"sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","role":"authenticated"}',
  true
);
insert into queue_test_state (job_id)
select (public.enqueue_pdf_ingestion(
  '10000000-0000-4000-8000-000000000001'
) ->> 'id')::uuid;

select is(
  (select status from public.jobs where id = (select job_id from queue_test_state)),
  'queued',
  'owned PDF enqueue creates a queued job'
);
select is(
  (select payload_json #>> '{input,objectPath}' from public.jobs
   where id = (select job_id from queue_test_state)),
  'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/10000000-0000-4000-8000-000000000001/original/source.pdf',
  'job payload contains the exact owned Storage path'
);
select ok(
  (select
     payload_json ->> 'schemaVersion' = '1'
     and payload_json ->> 'jobId' = id::text
     and payload_json ->> 'userId' = user_id::text
     and payload_json ->> 'jobType' = 'ingest_pdf'
     and payload_json #>> '{input,assetId}' = asset_id::text
     and payload_json #>> '{input,storageBucket}' = 'asset-private'
   from public.jobs where id = (select job_id from queue_test_state)),
  'queued payload conforms to the versioned Job Payload v1 identity contract'
);
select is(
  (public.enqueue_pdf_ingestion('10000000-0000-4000-8000-000000000001') ->> 'id')::uuid,
  (select job_id from queue_test_state),
  'duplicate enqueue returns the same logical job'
);
select is(
  (select count(*) from public.jobs),
  1::bigint,
  'duplicate enqueue does not create another job'
);
select throws_matching(
  $$select public.enqueue_pdf_ingestion('20000000-0000-4000-8000-000000000002')$$,
  'owned PDF asset not found',
  'user A cannot enqueue user B asset'
);
select is(
  (select count(*) from public.jobs),
  1::bigint,
  'user A sees exactly the owned job'
);
select throws_matching(
  $$update public.jobs set priority = 1000$$,
  'permission denied',
  'authenticated users cannot mutate queue state'
);

reset role;
set local role authenticated;
select set_config(
  'request.jwt.claims',
  '{"sub":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","role":"authenticated"}',
  true
);
select is((select count(*) from public.jobs), 0::bigint, 'user B cannot see user A jobs');

reset role;
set local role service_role;
select is(
  (select status from public.claim_job('worker-1', 'lightweight', 120)),
  'running',
  'worker atomically claims the queued job'
);
select results_eq(
  $$select attempt_count, lease_generation from public.jobs
    where id = (select job_id from queue_test_state)$$,
  $$values (1, 1::bigint)$$,
  'claim increments attempt count and fencing generation'
);
select is(
  (select count(*) from public.claim_job('worker-other', 'lightweight', 120)),
  0::bigint,
  'an actively leased job cannot be claimed twice'
);
select is(
  public.heartbeat_job(
    (select job_id from queue_test_state), 'worker-1', 0, 120
  ),
  false,
  'heartbeat rejects a stale fencing generation'
);
select is(
  public.heartbeat_job(
    (select job_id from queue_test_state), 'worker-1', 1, 120
  ),
  true,
  'heartbeat extends the current lease'
);

update public.jobs set lease_expires_at = now() - interval '1 second'
where id = (select job_id from queue_test_state);
select is(
  (select lease_generation from public.claim_job('worker-2', 'lightweight', 120)),
  2::bigint,
  'an expired lease is reclaimed with a new fencing generation'
);
select is(
  (select outcome from public.job_attempts
   where job_id = (select job_id from queue_test_state) and lease_generation = 1),
  'lease_expired',
  'reclaim closes the expired attempt'
);
select is(
  public.report_job_failure(
    (select job_id from queue_test_state), 'worker-1', 1,
    'transient', 'stale', 'stale worker', true
  ),
  null,
  'a stale worker cannot commit failure state'
);
select is(
  public.report_job_failure(
    (select job_id from queue_test_state), 'worker-2', 2,
    'transient', 'temporary_io', 'Temporary storage failure.', true
  ),
  'retry_wait',
  'a transient error schedules a bounded retry'
);
select is(
  (select status from public.assets
   where id = '10000000-0000-4000-8000-000000000001'),
  'pending',
  'retry returns the Asset to pending'
);

update public.jobs set next_run_at = now()
where id = (select job_id from queue_test_state);
select is(
  (select lease_generation from public.claim_job('worker-3', 'lightweight', 120)),
  3::bigint,
  'the retry can be claimed as a new fenced attempt'
);
select is(
  public.report_job_failure(
    (select job_id from queue_test_state), 'worker-3', 3,
    'unsupported', 'scanned_pdf', 'PDF contains no selectable text.', false
  ),
  'failed',
  'unsupported input terminates without retry'
);
select is(
  (select status from public.assets
   where id = '10000000-0000-4000-8000-000000000001'),
  'unsupported',
  'unsupported input is visible on the Asset'
);
select is(
  (select count(*) from public.job_attempts
   where job_id = (select job_id from queue_test_state)),
  3::bigint,
  'every claim has exactly one attempt record'
);
select is(
  (select count(*) from public.job_attempts where ended_at is null),
  0::bigint,
  'terminal failure closes the active attempt'
);
select is(
  (select last_error_code from public.jobs
   where id = (select job_id from queue_test_state)),
  'scanned_pdf',
  'job retains a safe terminal error code'
);
select is(
  (select count(*) from public.jobs where status = 'failed'),
  1::bigint,
  'the logical job has one terminal row'
);

select * from finish();
rollback;
