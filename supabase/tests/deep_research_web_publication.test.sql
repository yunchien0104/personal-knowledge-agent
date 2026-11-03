begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(27);

select has_table('public', 'deep_web_sources', 'Web source lifecycle table exists');
select has_table('public', 'deep_web_evidence', 'run-scoped Web Evidence table exists');
select has_table('public', 'deep_research_results', 'verified result table exists');
select has_table('public', 'deep_web_citations', 'Web citation snapshot table exists');
select ok(not has_function_privilege('authenticated',
  'public.append_deep_web_evidence(uuid,text,bigint,uuid,jsonb)', 'execute'),
  'authenticated clients cannot capture Web Evidence');
select ok(has_function_privilege('service_role',
  'public.publish_deep_research_draft(uuid,text,bigint,text,text,text,text,text,text,jsonb)',
  'execute'), 'service role can call the fenced Deep publication path');

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values (
  '00000000-0000-0000-0000-000000000000', 'e1000000-0000-4000-8000-000000000001',
  'authenticated', 'authenticated', 'deep-web@example.test', '', now(),
  '{"provider":"email","providers":["email"]}', '{}', now(), now()
);
insert into public.deep_research_account_settings (
  user_id, web_auto_authorized, max_cost_usd, monthly_research_limit_usd
) values ('e1000000-0000-4000-8000-000000000001', true, 0.5000, 10.0000);

create temporary table web_state (run_id uuid, job_id uuid, note_id uuid);
grant select, update, insert on web_state to authenticated, service_role;
set local role authenticated;
set local request.jwt.claim.sub = 'e1000000-0000-4000-8000-000000000001';
insert into web_state
select (result ->> 'id')::uuid, (result ->> 'jobId')::uuid
from (select public.start_deep_research(
  'Verify current public sources', 'e2000000-0000-4000-8000-000000000001',
  null, 'auto', '{"maxCostUsd":0.5}'::jsonb, null
) result) started;
reset role;
set local role service_role;
select is((select status from public.claim_job('web-worker', 'research', 120)),
  'running', 'Auto Web research job is claimed');

select is(public.reserve_deep_research_operation(
  (select job_id from web_state), 'web-worker', 1,
  'e3000000-0000-4000-8000-000000000001', 'web:first',
  'discover_web', 'web', repeat('a',64), 0.0100
) ->> 'status', 'reserved', 'Web operation receives an atomic reservation');
select ok(public.start_deep_research_operation(
  (select job_id from web_state), 'web-worker', 1,
  'e3000000-0000-4000-8000-000000000001'
), 'Web operation starts behind the lease fence');
select is(public.append_deep_web_evidence(
  (select job_id from web_state), 'web-worker', 1,
  'e3000000-0000-4000-8000-000000000001',
  jsonb_build_array(jsonb_build_object(
    'schemaVersion','evidence-record-v1','evidenceId','e4000000-0000-4000-8000-000000000001',
    'runId',(select run_id from web_state),'userId','e1000000-0000-4000-8000-000000000001',
    'origin','web','sourceIdentity','https://example.com/report',
    'sourceMetadata',jsonb_build_object('title','Report','provider','mock-web'),
    'excerpt','Verified public statement version one','citationLocator',jsonb_build_object('kind','web_page','url','https://example.com/report'),
    'contentHash',repeat('b',64),'capturedAt',now(),'storageScope','research_run',
    'citationEligible',true,'validityStatus','valid','isSearchSnippet',false
  ))
) ->> 'processedEvidence', '1', 'Fetched page is captured as Web Evidence');
select is((public.load_deep_research_state(
  (select job_id from web_state), 'web-worker', 1
) #>> '{evidence,0,origin}'), 'web', 'resume bundle contains Web Evidence provenance');

select is(public.record_deep_research_result(
  (select job_id from web_state), 'web-worker', 1,
  'verified_partial', 'max_rounds_reached', 'Partial result', 'Verified partial body',
  '[{"text":"Partial verified claim","uncertainty":"Partial","citations":[]}]'::jsonb,
  '{"gate":"passed","published":false}'::jsonb, repeat('c',64)
) ->> 'resultStatus', 'verified_partial', 'verified partial result is persisted without publication');
select is((select count(*) from public.notes where title = 'Partial result'), 0::bigint,
  'verified partial persistence does not create a Note');

select is(public.reserve_deep_research_operation(
  (select job_id from web_state), 'web-worker', 1,
  'e3000000-0000-4000-8000-000000000002', 'web:second',
  'fetch_web', 'web', repeat('d',64), 0.0100
) ->> 'status', 'reserved', 'second Web capture is reserved');
select ok(public.start_deep_research_operation(
  (select job_id from web_state), 'web-worker', 1,
  'e3000000-0000-4000-8000-000000000002'
), 'second Web capture starts');
select is(public.append_deep_web_evidence(
  (select job_id from web_state), 'web-worker', 1,
  'e3000000-0000-4000-8000-000000000002',
  jsonb_build_array(jsonb_build_object(
    'schemaVersion','evidence-record-v1','evidenceId','e4000000-0000-4000-8000-000000000002',
    'runId',(select run_id from web_state),'userId','e1000000-0000-4000-8000-000000000001',
    'origin','web','sourceIdentity','https://example.com/report',
    'sourceMetadata',jsonb_build_object('title','Updated Report','provider','mock-web'),
    'excerpt','Updated public statement version two','citationLocator',jsonb_build_object('kind','web_page','url','https://example.com/report'),
    'contentHash',repeat('e',64),'capturedAt',now(),'storageScope','research_run',
    'citationEligible',true,'validityStatus','valid','isSearchSnippet',false
  ))
) ->> 'processedEvidence', '1', 'updated source content is captured separately');
select is((select validity_status from public.deep_web_evidence
  where id = 'e4000000-0000-4000-8000-000000000001'),
  'superseded', 'older source content is marked superseded');
select ok(public.mark_deep_web_source_status(
  (select job_id from web_state), 'web-worker', 1,
  'https://example.com/report', 'deleted'
), 'source deletion is recorded');
select is((select count(*) from public.deep_web_evidence where citation_eligible),
  0::bigint, 'deleted source versions cannot be cited');
select throws_ok(
  $$select public.publish_deep_research_draft(
    (select job_id from web_state), 'web-worker', 1,
    'mock','mock-v1','deep-r2ef-v1',repeat('f',64),'Invalid draft','Body',
    '[{"text":"Deleted claim","uncertainty":"None","citations":[{"evidenceId":"e4000000-0000-4000-8000-000000000002","quote":"Updated public statement","relationType":"supports"}]}]'::jsonb
  )$$,
  '23000', 'Deep Research citation is not supported by current Evidence',
  'deleted Web Evidence cannot pass the publication gate'
);

select is(public.reserve_deep_research_operation(
  (select job_id from web_state), 'web-worker', 1,
  'e3000000-0000-4000-8000-000000000003', 'web:publishable',
  'fetch_web', 'web', repeat('1',64), 0.0100
) ->> 'status', 'reserved', 'publishable Web capture is reserved');
select ok(public.start_deep_research_operation(
  (select job_id from web_state), 'web-worker', 1,
  'e3000000-0000-4000-8000-000000000003'
), 'publishable Web capture starts');
select is(public.append_deep_web_evidence(
  (select job_id from web_state), 'web-worker', 1,
  'e3000000-0000-4000-8000-000000000003',
  jsonb_build_array(jsonb_build_object(
    'schemaVersion','evidence-record-v1','evidenceId','e4000000-0000-4000-8000-000000000003',
    'runId',(select run_id from web_state),'userId','e1000000-0000-4000-8000-000000000001',
    'origin','web','sourceIdentity','https://example.org/current',
    'sourceMetadata',jsonb_build_object('title','Current Source','provider','mock-web'),
    'excerpt','Current verified statement for publication','citationLocator',jsonb_build_object('kind','web_page','url','https://example.org/current'),
    'contentHash',repeat('2',64),'capturedAt',now(),'storageScope','research_run',
    'citationEligible',true,'validityStatus','valid','isSearchSnippet',false
  ))
) ->> 'processedEvidence', '1', 'publishable Web Evidence is captured');
update web_state set note_id = (
  public.publish_deep_research_draft(
    job_id, 'web-worker', 1, 'mock','mock-v1','deep-r2ef-v1',repeat('3',64),
    'Verified Web draft','Verified body',
    '[{"text":"Current verified claim","uncertainty":"Source-supported fact.","citations":[{"evidenceId":"e4000000-0000-4000-8000-000000000003","quote":"Current verified statement","relationType":"supports"}]}]'::jsonb
  ) ->> 'noteId'
)::uuid;
select isnt((select note_id from web_state), null::uuid,
  'verified Web publication creates a draft Note');
select is((select count(*) from public.deep_web_citations
  where research_run_id = (select run_id from web_state)), 1::bigint,
  'Web citation publication creates one provenance snapshot');
select is((select canonical_url from public.deep_web_citations),
  'https://example.org/current', 'Web citation snapshot retains canonical URL');
select is((select result_status from public.deep_research_results),
  'verified_complete', 'published Web result replaces the partial result atomically');

select * from finish();
rollback;
