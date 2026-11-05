begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(18);

select has_table('public','research_payload_manifests',
  'durable R2A payload manifests exist');
select has_function('public','record_deep_research_payload_manifest',
  array['uuid','text','bigint','text','jsonb'],'lease-fenced manifest RPC exists');
select ok((select relrowsecurity and relforcerowsecurity from pg_class
  where oid='public.research_payload_manifests'::regclass),
  'payload manifests use forced RLS');
select ok(not has_table_privilege('authenticated',
  'public.research_payload_manifests','insert'),
  'authenticated users cannot insert payload manifests');
select ok(has_table_privilege('authenticated',
  'public.research_payload_manifests','select'),
  'authenticated owners can read payload manifests');
select ok(not has_function_privilege('authenticated',
  'public.record_deep_research_payload_manifest(uuid,text,bigint,text,jsonb)',
  'execute'),'authenticated users cannot call Worker manifest RPC');
select ok(has_function_privilege('service_role',
  'public.record_deep_research_payload_manifest(uuid,text,bigint,text,jsonb)',
  'execute'),'service role can call Worker manifest RPC');

insert into auth.users (
  instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,
  raw_app_meta_data,raw_user_meta_data,created_at,updated_at
) values
('00000000-0000-0000-0000-000000000000','fc000000-0000-4000-8000-000000000001',
 'authenticated','authenticated','manifest-a@example.test','',now(),
 '{"provider":"email","providers":["email"]}','{}',now(),now()),
('00000000-0000-0000-0000-000000000000','fd000000-0000-4000-8000-000000000001',
 'authenticated','authenticated','manifest-b@example.test','',now(),
 '{"provider":"email","providers":["email"]}','{}',now(),now());

insert into public.deep_research_account_settings (
  user_id,max_cost_usd,daily_research_limit_usd,monthly_research_limit_usd
) values ('fc000000-0000-4000-8000-000000000001',1.0000,2.0000,10.0000);

create temporary table manifest_state (run_id uuid, job_id uuid, manifest jsonb);
grant select,insert,update on manifest_state to authenticated,service_role;
set local role authenticated;
set local request.jwt.claim.sub='fc000000-0000-4000-8000-000000000001';
insert into manifest_state (run_id,job_id)
select (value->>'id')::uuid,(value->>'jobId')::uuid
from (select public.start_deep_research(
  'Payload manifest test','fc000000-0000-4000-8000-000000000002',
  null,'disabled','{"maxRounds":1,"maxQueries":1,"maxCostUsd":1.0,"maxDurationSeconds":300}'::jsonb,
  null
) value) started;
reset role;

update manifest_state set manifest = jsonb_build_object(
  'schemaVersion','r2a-payload-manifest-v1',
  'algorithmVersion','extractive-coverage-v1',
  'manifestHash',repeat('a',64),
  'originalInputUpperBound',59655,
  'shapedInputUpperBound',47000,
  'configuredInputLimit',48000,
  'includedEvidenceIds',jsonb_build_array('fc000000-0000-4000-8000-000000000010'),
  'excludedEvidence','[]'::jsonb,
  'partiallyIncludedEvidence',jsonb_build_array(jsonb_build_object(
    'evidenceId','fc000000-0000-4000-8000-000000000010','omittedBytes',12655)),
  'coverageImpact',jsonb_build_array(jsonb_build_object(
    'requirementId','req_01','payloadCoverage','represented'))
);

set local role service_role;
select is((select status from public.claim_job('manifest-worker','research',120)),
  'running','manifest test job is leased');
select is(public.record_deep_research_payload_manifest(
  (select job_id from manifest_state),'manifest-worker',1,'r2a_synthesis',
  (select manifest from manifest_state)
)->>'stage','r2a_synthesis','manifest is recorded before the provider boundary');
select is((select count(*) from public.research_payload_manifests),1::bigint,
  'one manifest is durable');
select is(public.record_deep_research_payload_manifest(
  (select job_id from manifest_state),'manifest-worker',1,'r2a_synthesis',
  (select manifest from manifest_state)
)->>'idempotent','true','exact replay is idempotent');
select is((select count(*) from public.research_payload_manifests),1::bigint,
  'idempotent replay does not duplicate manifests');
select throws_ok($sql$select public.record_deep_research_payload_manifest(
  (select job_id from manifest_state),'manifest-worker',1,'r2a_synthesis',
  (select manifest || jsonb_build_object('shapedInputUpperBound',46000)
   from manifest_state)
)$sql$,'23000','research payload manifest conflicts with durable record',
  'conflicting replay cannot change paid-operation input');
select is(public.record_deep_research_payload_manifest(
  (select job_id from manifest_state),'manifest-worker',2,'r2a_synthesis',
  (select manifest from manifest_state)
),null::jsonb,'stale lease cannot write or read a manifest');

set local role authenticated;
set local request.jwt.claim.sub='fc000000-0000-4000-8000-000000000001';
select is((select count(*) from public.research_payload_manifests),1::bigint,
  'owner can read the payload manifest');
set local request.jwt.claim.sub='fd000000-0000-4000-8000-000000000001';
select is((select count(*) from public.research_payload_manifests),0::bigint,
  'cross-user payload manifest reads are denied');
reset role;
set local role service_role;
select throws_ok($$update public.research_payload_manifests
  set manifest_json=manifest_json$$,'23000','research payload manifests are append-only',
  'payload manifests cannot be updated');
select throws_ok($$delete from public.research_payload_manifests$$,
  '23000','research payload manifests are append-only',
  'payload manifests cannot be deleted');

select * from finish();
rollback;
