begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(34);

select ok(not has_function_privilege('anon', 'public.request_asset_deletion(uuid,text,uuid)', 'execute'), 'anon cannot request deletion');
select ok(has_function_privilege('authenticated', 'public.request_asset_deletion(uuid,text,uuid)', 'execute'), 'authenticated can request owned deletion');
select ok(not has_function_privilege('authenticated', 'public.get_asset_deletion_context(uuid,text,bigint)', 'execute'), 'client cannot read Worker deletion context');
select ok(has_function_privilege('service_role', 'public.get_asset_deletion_context(uuid,text,bigint)', 'execute'), 'Worker can read fenced deletion context');
select ok(not has_function_privilege('authenticated', 'public.complete_asset_deletion(uuid,text,bigint)', 'execute'), 'client cannot finalize deletion');
select ok(has_function_privilege('service_role', 'public.complete_asset_deletion(uuid,text,bigint)', 'execute'), 'Worker can finalize deletion');
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.asset_deletions'::regclass), 'deletion records use forced RLS');

create function pg_temp.claim_test_deletion_job(
  p_user_id uuid,
  p_request_key uuid,
  p_worker_id text
)
returns text
language plpgsql
set search_path = ''
as $$
declare
  v_expected_job_id uuid;
  v_next_job_id uuid;
  v_claimed_job public.jobs%rowtype;
begin
  select job_id into strict v_expected_job_id
  from public.asset_deletions
  where user_id = p_user_id and request_key = p_request_key;

  -- Put only this test's job first. Existing Queue rows remain untouched.
  update public.jobs
  set priority = 1000, created_at = '2000-01-01 00:00:00+00'::timestamptz
  where id = v_expected_job_id
    and user_id = p_user_id
    and resource_class = 'deletion';
  if not found then
    return 'test_job_missing';
  end if;

  -- Fail safely instead of allowing claim_job() to lease a non-test job.
  select id into v_next_job_id
  from public.jobs
  where cancel_requested_at is null
    and resource_class = 'deletion'
    and (
      (status in ('queued', 'retry_wait') and next_run_at <= now())
      or (status = 'running' and lease_expires_at <= now())
    )
  order by (
    priority + least(
      100,
      greatest(0, floor(extract(epoch from (now() - created_at)) / 3600)::integer)
    )
  ) desc, created_at, id
  limit 1;

  if v_next_job_id is distinct from v_expected_job_id then
    return 'refused_non_test_job';
  end if;

  select * into v_claimed_job
  from public.claim_job(p_worker_id, 'deletion', 120);
  if not found or v_claimed_job.id is distinct from v_expected_job_id then
    return 'refused_non_test_job';
  end if;
  return v_claimed_job.status;
end;
$$;

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values
  ('00000000-0000-0000-0000-000000000000', '71000000-0000-4000-8000-000000000001',
   'authenticated', 'authenticated', 'delete-a@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now()),
  ('00000000-0000-0000-0000-000000000000', '71000000-0000-4000-8000-000000000002',
   'authenticated', 'authenticated', 'delete-b@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now());

insert into public.assets (id, user_id, source_type, status) values
  ('72000000-0000-4000-8000-000000000001', '71000000-0000-4000-8000-000000000001', 'pdf', 'ready'),
  ('72000000-0000-4000-8000-000000000002', '71000000-0000-4000-8000-000000000001', 'pdf', 'ready'),
  ('72000000-0000-4000-8000-000000000003', '71000000-0000-4000-8000-000000000002', 'pdf', 'ready');
insert into public.asset_revisions (
  id, user_id, asset_id, revision_no, fingerprint, processor_version, status
) values
  ('73000000-0000-4000-8000-000000000001', '71000000-0000-4000-8000-000000000001', '72000000-0000-4000-8000-000000000001', 1, repeat('a',64), 'test-v1', 'validated'),
  ('73000000-0000-4000-8000-000000000002', '71000000-0000-4000-8000-000000000001', '72000000-0000-4000-8000-000000000002', 1, repeat('b',64), 'test-v1', 'validated'),
  ('73000000-0000-4000-8000-000000000003', '71000000-0000-4000-8000-000000000002', '72000000-0000-4000-8000-000000000003', 1, repeat('c',64), 'test-v1', 'validated');
update public.assets as asset
set active_revision_id = test_revision.revision_id
from (values
  ('72000000-0000-4000-8000-000000000001'::uuid, '71000000-0000-4000-8000-000000000001'::uuid, '73000000-0000-4000-8000-000000000001'::uuid),
  ('72000000-0000-4000-8000-000000000002'::uuid, '71000000-0000-4000-8000-000000000001'::uuid, '73000000-0000-4000-8000-000000000002'::uuid),
  ('72000000-0000-4000-8000-000000000003'::uuid, '71000000-0000-4000-8000-000000000002'::uuid, '73000000-0000-4000-8000-000000000003'::uuid)
) as test_revision(asset_id, user_id, revision_id)
where asset.id = test_revision.asset_id
  and asset.user_id = test_revision.user_id;
insert into public.asset_evidence (
  id, user_id, revision_id, locator_type, locator_json, excerpt, source_hash
) values
  ('74000000-0000-4000-8000-000000000001', '71000000-0000-4000-8000-000000000001', '73000000-0000-4000-8000-000000000001', 'pdf', '{"page_number":1}', 'Asset one evidence', repeat('a',64)),
  ('74000000-0000-4000-8000-000000000002', '71000000-0000-4000-8000-000000000001', '73000000-0000-4000-8000-000000000002', 'pdf', '{"page_number":2}', 'Asset two evidence', repeat('b',64));

insert into public.notes (id, user_id, note_type, title, body) values
  ('75000000-0000-4000-8000-000000000001', '71000000-0000-4000-8000-000000000001', 'source_note', 'Source one', 'Summary'),
  ('75000000-0000-4000-8000-000000000002', '71000000-0000-4000-8000-000000000001', 'research_note', 'Shared research', 'Uses two sources');
insert into public.source_note_generations (
  id, user_id, note_id, asset_id, source_revision_id, asset_id_snapshot,
  source_revision_id_snapshot, source_fingerprint, job_id, model_provider,
  model_name, prompt_version, output_hash
) values (
  '76000000-0000-4000-8000-000000000001', '71000000-0000-4000-8000-000000000001',
  '75000000-0000-4000-8000-000000000001', '72000000-0000-4000-8000-000000000001',
  '73000000-0000-4000-8000-000000000001', '72000000-0000-4000-8000-000000000001',
  '73000000-0000-4000-8000-000000000001', repeat('a',64),
  '76000000-0000-4000-8000-000000000002', 'test', 'test-v1', 'test-v1', repeat('d',64)
);
insert into public.claims (
  id, user_id, note_id, text, claim_type, source_note_revision
) values (
  '77000000-0000-4000-8000-000000000001', '71000000-0000-4000-8000-000000000001',
  '75000000-0000-4000-8000-000000000001', 'Source claim', 'source_assertion', 1
);
insert into public.claim_evidence (
  id, user_id, claim_id, evidence_id, evidence_id_snapshot, source_revision_id,
  source_revision_id_snapshot, relation_type, locator_json, supporting_quote
) values (
  '78000000-0000-4000-8000-000000000001', '71000000-0000-4000-8000-000000000001',
  '77000000-0000-4000-8000-000000000001', '74000000-0000-4000-8000-000000000001',
  '74000000-0000-4000-8000-000000000001', '73000000-0000-4000-8000-000000000001',
  '73000000-0000-4000-8000-000000000001', 'asserts', '{"page_number":1}', 'Asset one evidence'
);

insert into public.jobs (
  id, user_id, asset_id, job_type, payload_json, status, resource_class,
  idempotency_key, attempt_count, lease_owner, lease_expires_at, lease_generation
) values (
  '79000000-0000-4000-8000-000000000001', '71000000-0000-4000-8000-000000000001',
  '72000000-0000-4000-8000-000000000001', 'ingest_pdf',
  '{"schemaVersion":1,"jobId":"79000000-0000-4000-8000-000000000001","userId":"71000000-0000-4000-8000-000000000001","jobType":"ingest_pdf","idempotencyKey":"running-ingest","input":{"assetId":"72000000-0000-4000-8000-000000000001"}}',
  'running', 'lightweight', 'running-ingest', 1, 'old-worker', now() + interval '2 minutes', 1
), (
  '79000000-0000-4000-8000-000000000002', '71000000-0000-4000-8000-000000000001',
  null, 'execute_research',
  '{"schemaVersion":1,"jobId":"79000000-0000-4000-8000-000000000002","userId":"71000000-0000-4000-8000-000000000001","jobType":"execute_research","idempotencyKey":"shared-research","input":{"researchRunId":"7a000000-0000-4000-8000-000000000001"}}',
  'completed', 'research', 'shared-research', 1, null, null, 1
);
insert into public.job_attempts (
  user_id, job_id, attempt_no, lease_generation, worker_id
) values (
  '71000000-0000-4000-8000-000000000001', '79000000-0000-4000-8000-000000000001', 1, 1, 'old-worker'
);
insert into public.research_runs (
  id, user_id, request_key, question, status, job_id, output_note_id
) values (
  '7a000000-0000-4000-8000-000000000001', '71000000-0000-4000-8000-000000000001',
  '7a000000-0000-4000-8000-000000000002', 'Compare sources', 'draft_ready',
  '79000000-0000-4000-8000-000000000002', '75000000-0000-4000-8000-000000000002'
);
insert into public.research_citations (
  id, user_id, research_run_id, note_id, finding_index, citation_index,
  relation_type, evidence_id, evidence_id_snapshot, asset_id, asset_id_snapshot,
  source_revision_id, source_revision_id_snapshot, locator_json, supporting_quote
) values
  ('7b000000-0000-4000-8000-000000000001', '71000000-0000-4000-8000-000000000001', '7a000000-0000-4000-8000-000000000001', '75000000-0000-4000-8000-000000000002', 0, 0, 'supports', '74000000-0000-4000-8000-000000000001', '74000000-0000-4000-8000-000000000001', '72000000-0000-4000-8000-000000000001', '72000000-0000-4000-8000-000000000001', '73000000-0000-4000-8000-000000000001', '73000000-0000-4000-8000-000000000001', '{"page_number":1}', 'Asset one evidence'),
  ('7b000000-0000-4000-8000-000000000002', '71000000-0000-4000-8000-000000000001', '7a000000-0000-4000-8000-000000000001', '75000000-0000-4000-8000-000000000002', 0, 1, 'supports', '74000000-0000-4000-8000-000000000002', '74000000-0000-4000-8000-000000000002', '72000000-0000-4000-8000-000000000002', '72000000-0000-4000-8000-000000000002', '73000000-0000-4000-8000-000000000002', '73000000-0000-4000-8000-000000000002', '{"page_number":2}', 'Asset two evidence');

set local role authenticated;
set local request.jwt.claim.sub = '71000000-0000-4000-8000-000000000001';
select throws_matching(
  $$select public.request_asset_deletion('72000000-0000-4000-8000-000000000003', 'purge_all', '7c000000-0000-4000-8000-000000000001')$$,
  'owned PDF asset not found', 'another user cannot request deletion'
);
select is(
  (public.request_asset_deletion('72000000-0000-4000-8000-000000000001', 'remove_original', '7c000000-0000-4000-8000-000000000002') ->> 'id')::uuid,
  (public.request_asset_deletion('72000000-0000-4000-8000-000000000001', 'remove_original', '7c000000-0000-4000-8000-000000000002') ->> 'id')::uuid,
  'same request key is idempotent'
);
select is((select status from public.assets where id = '72000000-0000-4000-8000-000000000001'), 'deleting', 'request immediately fences the Asset');
select is((select active_revision_id from public.assets where id = '72000000-0000-4000-8000-000000000001'), null::uuid, 'request removes the active Revision pointer');
select is((select status from public.jobs where id = '79000000-0000-4000-8000-000000000001'), 'cancelled', 'running Asset job is cancelled');
select is((select count(*) from public.search_knowledge('Asset one', 20, 0)), 0::bigint, 'deleting source is immediately excluded from Retrieval');
select is((select count(*) from public.search_knowledge('Asset two', 20, 0)), 1::bigint, 'other active source remains retrievable');

reset role;
set local role service_role;
select is(public.heartbeat_job('79000000-0000-4000-8000-000000000001', 'old-worker', 1, 120), false, 'cancelled Worker loses its fence');
select is(pg_temp.claim_test_deletion_job('71000000-0000-4000-8000-000000000001', '7c000000-0000-4000-8000-000000000002', 'delete-worker'), 'running', 'deletion Worker claims isolated work');
select is((public.get_asset_deletion_context((select job_id from public.asset_deletions where user_id = '71000000-0000-4000-8000-000000000001' and request_key = '7c000000-0000-4000-8000-000000000002'), 'delete-worker', 1) ->> 'storagePrefix'), '71000000-0000-4000-8000-000000000001/72000000-0000-4000-8000-000000000001/original', 'remove-original context is prefix scoped');
select is((public.complete_asset_deletion((select job_id from public.asset_deletions where user_id = '71000000-0000-4000-8000-000000000001' and request_key = '7c000000-0000-4000-8000-000000000002'), 'delete-worker', 1) ->> 'status'), 'completed', 'Worker completes remove-original');
select is((select status from public.assets where id = '72000000-0000-4000-8000-000000000001'), 'source_removed', 'source record remains marked as removed');
select is((select count(*) from public.asset_evidence where id = '74000000-0000-4000-8000-000000000001'), 1::bigint, 'remove-original preserves derived Evidence rows');
select is((select count(*) from public.notes where id = '75000000-0000-4000-8000-000000000001'), 1::bigint, 'remove-original preserves source Note');
select is((select validity_status from public.claim_evidence where id = '78000000-0000-4000-8000-000000000001'), 'source_deleted', 'source claim citation is invalidated');

reset role;
set local role authenticated;
set local request.jwt.claim.sub = '71000000-0000-4000-8000-000000000001';
select is((public.request_asset_deletion('72000000-0000-4000-8000-000000000001', 'purge_all', '7c000000-0000-4000-8000-000000000003') ->> 'status'), 'pending', 'removed source may later be purged');

reset role;
set local role service_role;
select is(pg_temp.claim_test_deletion_job('71000000-0000-4000-8000-000000000001', '7c000000-0000-4000-8000-000000000003', 'delete-worker'), 'running', 'purge job is claimable');
select is((public.complete_asset_deletion((select job_id from public.asset_deletions where user_id = '71000000-0000-4000-8000-000000000001' and request_key = '7c000000-0000-4000-8000-000000000003'), 'delete-worker', 1) ->> 'status'), 'completed', 'Worker completes purge');
select is((select count(*) from public.assets where id = '72000000-0000-4000-8000-000000000001'), 0::bigint, 'purge deletes the Asset');
select is((select count(*) from public.notes where id = '75000000-0000-4000-8000-000000000001'), 0::bigint, 'purge deletes only the source-specific Note');
select is((select count(*) from public.asset_evidence where id = '74000000-0000-4000-8000-000000000001'), 0::bigint, 'purge deletes source Evidence');
select is((select count(*) from public.notes where id = '75000000-0000-4000-8000-000000000002'), 1::bigint, 'shared Research Note is preserved');
select is((select count(*) from public.research_runs where id = '7a000000-0000-4000-8000-000000000001'), 1::bigint, 'Research history is preserved');
select ok((select asset_id is null and asset_id_snapshot = '72000000-0000-4000-8000-000000000001' from public.research_citations where id = '7b000000-0000-4000-8000-000000000001'), 'purged citation keeps snapshot but loses live link');
select is((select asset_id from public.research_citations where id = '7b000000-0000-4000-8000-000000000002'), '72000000-0000-4000-8000-000000000002'::uuid, 'other source citation remains live');
select is((select status from public.asset_deletions where user_id = '71000000-0000-4000-8000-000000000001' and request_key = '7c000000-0000-4000-8000-000000000003'), 'completed', 'backend confirmation is the completion boundary');

reset role;
set local role authenticated;
set local request.jwt.claim.sub = '71000000-0000-4000-8000-000000000002';
select is((select count(*) from public.asset_deletions), 0::bigint, 'RLS hides another user deletion history');

reset role;
select * from finish();
rollback;
