-- This test intentionally commits an isolated fixture so two dblink sessions can
-- see it. Cleanup is exact and removes only the fixed test Auth identity.

create extension if not exists pgtap with schema extensions;
create temporary table dblink_extension_state (was_present boolean not null);
insert into dblink_extension_state
select exists (select 1 from pg_extension where extname = 'dblink');
create extension if not exists dblink with schema extensions;
set search_path = public, extensions;

select plan(5);

delete from auth.users where id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values (
  '00000000-0000-0000-0000-000000000000',
  'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  'authenticated', 'authenticated', 'queue-concurrency@example.test', '', now(),
  '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now()
);
insert into public.assets (id, user_id, source_type) values (
  '30000000-0000-4000-8000-000000000003',
  'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  'pdf'
);
insert into public.jobs (
  id, user_id, asset_id, job_type, payload_json, idempotency_key
) values (
  '40000000-0000-4000-8000-000000000004',
  'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  '30000000-0000-4000-8000-000000000003',
  'ingest_pdf',
  jsonb_build_object(
    'schemaVersion', 1,
    'jobId', '40000000-0000-4000-8000-000000000004',
    'userId', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
    'jobType', 'ingest_pdf',
    'idempotencyKey', 'concurrent-claim-v1',
    'input', jsonb_build_object(
      'assetId', '30000000-0000-4000-8000-000000000003'
    )
  ),
  'concurrent-claim-v1'
);

create temporary table concurrent_sessions (worker_id text primary key, backend_pid integer);
create temporary table concurrent_claims (worker_id text not null, job_id uuid not null);

do $$
declare
  -- Route through the published local port so PostgreSQL 17 consumes the
  -- supplied SCRAM password; direct in-container trust auth is rejected by
  -- dblink_connect for the non-superuser `postgres` role in Supabase Local.
  connection_string text := format(
    'host=host.docker.internal port=54322 dbname=%L user=postgres password=postgres',
    current_database()
  );
begin
  perform extensions.dblink_connect('queue_worker_one', connection_string);
  perform extensions.dblink_connect('queue_worker_two', connection_string);
end;
$$;

insert into concurrent_sessions
select 'worker-one', backend_pid
from extensions.dblink('queue_worker_one', 'select pg_backend_pid()')
  as session(backend_pid integer);
insert into concurrent_sessions
select 'worker-two', backend_pid
from extensions.dblink('queue_worker_two', 'select pg_backend_pid()')
  as session(backend_pid integer);

select isnt(
  (select backend_pid from concurrent_sessions where worker_id = 'worker-one'),
  (select backend_pid from concurrent_sessions where worker_id = 'worker-two'),
  'claim contenders use two independent PostgreSQL sessions'
);

do $$
begin
  perform extensions.dblink_exec('queue_worker_one', 'begin');
  perform extensions.dblink_exec('queue_worker_two', 'begin');
  perform extensions.dblink_exec('queue_worker_one', 'set local role service_role');
  perform extensions.dblink_exec(
    'queue_worker_one', 'set local statement_timeout = ''2s'''
  );
  perform extensions.dblink_exec('queue_worker_two', 'set local role service_role');
  perform extensions.dblink_exec(
    'queue_worker_two', 'set local statement_timeout = ''2s'''
  );
  perform extensions.dblink_send_query(
    'queue_worker_one',
    $query$select id from public.claim_job('concurrent-worker-one', 'lightweight', 120)$query$
  );
  perform extensions.dblink_send_query(
    'queue_worker_two',
    $query$select id from public.claim_job('concurrent-worker-two', 'lightweight', 120)$query$
  );
end;
$$;

insert into concurrent_claims
select 'worker-one', job_id
from extensions.dblink_get_result('queue_worker_one') as claimed(job_id uuid);
insert into concurrent_claims
select 'worker-two', job_id
from extensions.dblink_get_result('queue_worker_two') as claimed(job_id uuid);

-- PostgreSQL 17 keeps an asynchronous dblink connection busy until the final
-- empty result is consumed, even after the row-bearing result was read.
do $$
begin
  perform * from extensions.dblink_get_result('queue_worker_one') as drained(job_id uuid);
  perform * from extensions.dblink_get_result('queue_worker_two') as drained(job_id uuid);
end;
$$;

do $$
begin
  perform extensions.dblink_exec('queue_worker_one', 'commit');
  perform extensions.dblink_exec('queue_worker_two', 'commit');
end;
$$;

select is(
  (select count(*) from concurrent_claims),
  1::bigint,
  'two overlapping transactions claim exactly one job'
);
select is(
  (select count(distinct job_id) from concurrent_claims),
  1::bigint,
  'SKIP LOCKED never returns the same job to both workers'
);
select is(
  (select count(*) from public.job_attempts
   where job_id = '40000000-0000-4000-8000-000000000004'),
  1::bigint,
  'only the winning transaction creates an attempt'
);
select ok(
  (select status = 'running'
     and lease_owner in ('concurrent-worker-one', 'concurrent-worker-two')
     and lease_generation = 1
   from public.jobs where id = '40000000-0000-4000-8000-000000000004'),
  'the single winner owns generation one'
);

do $$
begin
  perform extensions.dblink_disconnect('queue_worker_one');
  perform extensions.dblink_disconnect('queue_worker_two');
end;
$$;
delete from auth.users where id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

do $$
begin
  if not (select was_present from dblink_extension_state) then
    execute 'drop extension dblink';
  end if;
end;
$$;

select * from finish();
