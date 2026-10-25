begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(33);

select ok(not has_function_privilege('anon', 'public.start_research(text,uuid)', 'execute'), 'anon cannot start research');
select ok(has_function_privilege('authenticated', 'public.start_research(text,uuid)', 'execute'), 'authenticated can start research');
select ok(not has_function_privilege('authenticated', 'public.get_research_context(uuid,text,bigint,jsonb)', 'execute'), 'authenticated cannot call Worker retrieval');
select ok(has_function_privilege('service_role', 'public.get_research_context(uuid,text,bigint,jsonb)', 'execute'), 'service role can retrieve fenced context');
select ok(not has_function_privilege('authenticated', 'public.publish_research_draft(uuid,text,bigint,text,text,text,text,text,text,jsonb)', 'execute'), 'authenticated cannot publish generated draft');
select ok(has_function_privilege('service_role', 'public.publish_research_draft(uuid,text,bigint,text,text,text,text,text,text,jsonb)', 'execute'), 'service role can publish fenced draft');
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.research_runs'::regclass), 'Research Runs use forced RLS');
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.research_citations'::regclass), 'Research citations use forced RLS');
select has_table('public', 'research_run_evidence', 'Research retrieval checkpoint table exists');
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.research_run_evidence'::regclass), 'Research retrieval checkpoints use forced RLS');
select ok(not has_function_privilege('authenticated',
  'public.hybrid_retrieve_for_user(uuid,text,extensions.vector,text,text,uuid[],integer,integer)',
  'execute'), 'authenticated cannot call the explicit-user SECURITY DEFINER helper');
select ok(has_function_privilege('service_role',
  'public.hybrid_retrieve_for_research_job(uuid,text,bigint,text,extensions.vector,text,text,uuid[],integer,integer)',
  'execute'), 'service role can call only the fenced Research retrieval RPC');
select ok(not has_function_privilege('service_role',
  'public.hybrid_retrieve_for_worker(uuid,text,extensions.vector,text,text,uuid[],integer,integer)',
  'execute'), 'unbound worker retrieval RPC is revoked');

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values
  ('00000000-0000-0000-0000-000000000000', 'a1000000-0000-4000-8000-000000000001',
   'authenticated', 'authenticated', 'research-a@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now()),
  ('00000000-0000-0000-0000-000000000000', 'a1000000-0000-4000-8000-000000000002',
   'authenticated', 'authenticated', 'research-b@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now());

insert into public.assets (id, user_id, source_type, status) values
  ('a2000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001', 'pdf', 'ready'),
  ('a2000000-0000-4000-8000-000000000002', 'a1000000-0000-4000-8000-000000000002', 'pdf', 'ready');
insert into public.asset_revisions (
  id, user_id, asset_id, revision_no, fingerprint, processor_version, status
) values
  ('a3000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001',
   'a2000000-0000-4000-8000-000000000001', 1, repeat('a', 64), 'test-v1', 'validated'),
  ('a3000000-0000-4000-8000-000000000002', 'a1000000-0000-4000-8000-000000000002',
   'a2000000-0000-4000-8000-000000000002', 1, repeat('b', 64), 'test-v1', 'validated');
update public.assets set active_revision_id = case id
  when 'a2000000-0000-4000-8000-000000000001' then 'a3000000-0000-4000-8000-000000000001'::uuid
  else 'a3000000-0000-4000-8000-000000000002'::uuid end;
insert into public.asset_evidence (
  id, user_id, revision_id, locator_type, locator_json, excerpt, source_hash
) values
  ('a4000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001',
   'a3000000-0000-4000-8000-000000000001', 'pdf',
   '{"kind":"pdf_page","page_number":2,"start_offset":0,"end_offset":48}',
   'Revenue increased by 20 percent with uncertainty.', repeat('a', 64)),
  ('a4000000-0000-4000-8000-000000000002', 'a1000000-0000-4000-8000-000000000002',
   'a3000000-0000-4000-8000-000000000002', 'pdf',
   '{"kind":"pdf_page","page_number":9,"start_offset":0,"end_offset":39}',
   'Revenue private evidence for another user.', repeat('b', 64));

create temporary table research_state (run_id uuid, job_id uuid, note_id uuid, plan_json jsonb);
grant select, update, insert on research_state to authenticated, service_role;
set local role authenticated;
set local request.jwt.claim.sub = 'a1000000-0000-4000-8000-000000000001';
insert into research_state (run_id, job_id)
select (result ->> 'id')::uuid, (result ->> 'jobId')::uuid
from (select public.start_research(
  'What does the evidence say about revenue?',
  'a5000000-0000-4000-8000-000000000001'
) result) started;
update research_state set plan_json = jsonb_build_object(
  'question', 'What does the evidence say about revenue?',
  'subquestions', jsonb_build_array('What changed in revenue?'),
  'lexicalQueries', jsonb_build_array('revenue increased'),
  'semanticQueries', jsonb_build_array('revenue change'),
  'searchKeywords', jsonb_build_array('revenue'),
  'maxCostUsd', 0, 'maxQueries', 1, 'maxRounds', 1
);
select is((select status from public.research_runs where id = (select run_id from research_state)), 'queued', 'explicit user request creates queued Research Run');
select is((select asset_id from public.jobs where id = (select job_id from research_state)), null::uuid, 'Research Job is not coupled to one Asset');
select is((select resource_class from public.jobs where id = (select job_id from research_state)), 'research', 'Research uses isolated Worker capability');
select is(
  (public.start_research('What does the evidence say about revenue?', 'a5000000-0000-4000-8000-000000000001') ->> 'id')::uuid,
  (select run_id from research_state), 'request key makes enqueue idempotent'
);

reset role;
set local role service_role;
select is((select status from public.claim_job('research-worker', 'research', 120)), 'running', 'Research Worker claims only Research work');
select is((select status from public.research_runs where id = (select run_id from research_state)), 'running', 'Run follows claimed Job state');
select is(
  public.get_research_context(
    (select job_id from research_state), 'research-worker', 1,
    (select plan_json from research_state)
  ) ->> 'question',
  'What does the evidence say about revenue?',
  'fenced context returns the trusted Research question without bulk Evidence'
);
select is(
  public.get_research_context(
    (select job_id from research_state), 'other-worker', 1,
    (select plan_json from research_state)
  ),
  null::jsonb, 'stale Worker identity cannot retrieve context'
);
select is(
  public.record_research_retrieval(
    (select job_id from research_state), 'research-worker', 1,
    (select plan_json from research_state), 'degraded_embedding_unavailable',
    '[{"id":"a4000000-0000-4000-8000-000000000001","assetId":"a2000000-0000-4000-8000-000000000001","revisionId":"a3000000-0000-4000-8000-000000000001","retrievalMethod":"lexical","retrievalScore":0.5,"queryHits":1}]'::jsonb
  ) ->> 'evidenceCount',
  '1', 'fenced retrieval checkpoint records only owned active Evidence'
);
select throws_matching(
  $$select public.publish_research_draft(
    (select job_id from research_state), 'research-worker', 1,
    'mock', 'mock-v1', 'research-internal-v1', repeat('c', 64),
    'Revenue Draft', 'Body',
    '[{"text":"Unsupported","uncertainty":"Unknown","citations":[{"evidenceId":"a4000000-0000-4000-8000-000000000001","quote":"Revenue doubled","relationType":"supports"}]}]'::jsonb
  )$$,
  'research citation is not supported by active original evidence', 'non-verbatim citation is rejected'
);
select is((select count(*) from public.notes where note_type = 'research_note'), 0::bigint, 'invalid citation publishes no draft');

update research_state set note_id = (
  public.publish_research_draft(
    job_id, 'research-worker', 1, 'mock', 'mock-v1', 'research-internal-v1',
    repeat('c', 64), 'Revenue Draft',
    '## Finding\nRevenue increased. [Evidence a4000000-0000-4000-8000-000000000001]',
    '[{"text":"Revenue increased.","uncertainty":"One source only.","citations":[{"evidenceId":"a4000000-0000-4000-8000-000000000001","quote":"Revenue increased by 20 percent","relationType":"supports"}]}]'::jsonb
  ) ->> 'noteId'
)::uuid;
select is((select note_type from public.notes where id = (select note_id from research_state)), 'research_note', 'publication creates a Research Note');
select is((select publication_state from public.notes where id = (select note_id from research_state)), 'draft', 'Research output remains a user-controlled draft');
select is((select status from public.research_runs where id = (select run_id from research_state)), 'draft_ready', 'Run records draft readiness');
select is((select count(*) from public.research_citations where research_run_id = (select run_id from research_state)), 1::bigint, 'draft stores citation snapshot');
select is((select locator_json ->> 'page_number' from public.research_citations where research_run_id = (select run_id from research_state)), '2', 'citation retains exact PDF page');
select is((select supporting_quote from public.research_citations where research_run_id = (select run_id from research_state)), 'Revenue increased by 20 percent', 'citation retains verbatim source quote');
select is(
  (public.publish_research_draft(
    (select job_id from research_state), 'research-worker', 1,
    'mock', 'mock-v1', 'research-internal-v1', repeat('c', 64),
    'ignored', 'ignored', '[]'::jsonb
  ) ->> 'noteId')::uuid,
  (select note_id from research_state), 'completed replay returns the same draft'
);

reset role;
set local role authenticated;
set local request.jwt.claim.sub = 'a1000000-0000-4000-8000-000000000002';
select is((select count(*) from public.research_runs), 0::bigint, 'other user cannot read Research Run');
select is((select count(*) from public.research_citations), 0::bigint, 'other user cannot read citations');

reset role;
select * from finish();
rollback;
