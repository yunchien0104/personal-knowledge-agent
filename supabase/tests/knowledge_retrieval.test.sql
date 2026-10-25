begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(25);

select ok(not has_function_privilege('anon', 'public.search_knowledge(text,integer,integer)', 'execute'), 'anon has no retrieval grant');
select ok(has_function_privilege('authenticated', 'public.search_knowledge(text,integer,integer)', 'execute'), 'authenticated has retrieval grant');
select ok(has_function_privilege('service_role', 'public.search_knowledge(text,integer,integer)', 'execute'), 'service role retains the explicitly deployed worker retrieval grant');
select ok(
  not exists (
    select 1
    from pg_proc p
    cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
    where p.oid = 'public.search_knowledge(text,integer,integer)'::regprocedure
      and a.grantee = 0
      and a.privilege_type = 'EXECUTE'
  ),
  'PUBLIC has no retrieval grant'
);

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values
  ('00000000-0000-0000-0000-000000000000', '81000000-0000-4000-8000-000000000001',
   'authenticated', 'authenticated', 'retrieval-a@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now()),
  ('00000000-0000-0000-0000-000000000000', '81000000-0000-4000-8000-000000000002',
   'authenticated', 'authenticated', 'retrieval-b@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now());

insert into public.assets (id, user_id, source_type, status) values
  ('82000000-0000-4000-8000-000000000001', '81000000-0000-4000-8000-000000000001', 'pdf', 'ready'),
  ('82000000-0000-4000-8000-000000000002', '81000000-0000-4000-8000-000000000002', 'pdf', 'ready');
insert into public.asset_revisions (
  id, user_id, asset_id, revision_no, fingerprint, processor_version, status
) values
  ('83000000-0000-4000-8000-000000000001', '81000000-0000-4000-8000-000000000001',
   '82000000-0000-4000-8000-000000000001', 1, repeat('a', 64), 'test-v1', 'validated'),
  ('83000000-0000-4000-8000-000000000002', '81000000-0000-4000-8000-000000000002',
   '82000000-0000-4000-8000-000000000002', 1, repeat('b', 64), 'test-v1', 'validated');
update public.assets set active_revision_id = case id
  when '82000000-0000-4000-8000-000000000001' then '83000000-0000-4000-8000-000000000001'::uuid
  else '83000000-0000-4000-8000-000000000002'::uuid end;
insert into public.asset_evidence (
  id, user_id, revision_id, locator_type, locator_json, excerpt, source_hash
) values
  ('84000000-0000-4000-8000-000000000001', '81000000-0000-4000-8000-000000000001',
   '83000000-0000-4000-8000-000000000001', 'pdf',
   '{"kind":"pdf_page","page_number":7,"start_offset":0,"end_offset":30}',
   'orion original source truth', repeat('a', 64)),
  ('84000000-0000-4000-8000-000000000002', '81000000-0000-4000-8000-000000000002',
   '83000000-0000-4000-8000-000000000002', 'pdf',
   '{"kind":"pdf_page","page_number":9,"start_offset":0,"end_offset":30}',
   'orion private beta source', repeat('b', 64));
insert into public.notes (id, user_id, note_type, title, body) values
  ('85000000-0000-4000-8000-000000000001', '81000000-0000-4000-8000-000000000001',
   'source_note', 'Orion analysis', 'orion generated note summary'),
  ('85000000-0000-4000-8000-000000000002', '81000000-0000-4000-8000-000000000002',
   'source_note', 'Orion private', 'orion private generated note');
insert into public.source_note_generations (
  user_id, note_id, asset_id, source_revision_id, asset_id_snapshot,
  source_revision_id_snapshot, source_fingerprint, job_id,
  model_provider, model_name, prompt_version, output_hash
) values
  ('81000000-0000-4000-8000-000000000001', '85000000-0000-4000-8000-000000000001',
   '82000000-0000-4000-8000-000000000001', '83000000-0000-4000-8000-000000000001',
   '82000000-0000-4000-8000-000000000001', '83000000-0000-4000-8000-000000000001',
   repeat('a', 64), '86000000-0000-4000-8000-000000000001', 'mock', 'mock', 'v1', repeat('c', 64)),
  ('81000000-0000-4000-8000-000000000002', '85000000-0000-4000-8000-000000000002',
   '82000000-0000-4000-8000-000000000002', '83000000-0000-4000-8000-000000000002',
   '82000000-0000-4000-8000-000000000002', '83000000-0000-4000-8000-000000000002',
   repeat('b', 64), '86000000-0000-4000-8000-000000000002', 'mock', 'mock', 'v1', repeat('d', 64));
insert into public.claims (
  id, user_id, note_id, text, claim_type, verification_status, needs_review, source_note_revision
) values
  ('87000000-0000-4000-8000-000000000001', '81000000-0000-4000-8000-000000000001',
   '85000000-0000-4000-8000-000000000001', 'orion generated claim', 'source_assertion', 'unreviewed', true, 1),
  ('87000000-0000-4000-8000-000000000002', '81000000-0000-4000-8000-000000000002',
   '85000000-0000-4000-8000-000000000002', 'orion private claim', 'source_assertion', 'unreviewed', true, 1);
insert into public.claim_evidence (
  user_id, claim_id, evidence_id, evidence_id_snapshot,
  source_revision_id, source_revision_id_snapshot, relation_type,
  locator_json, supporting_quote
) values
  ('81000000-0000-4000-8000-000000000001', '87000000-0000-4000-8000-000000000001',
   '84000000-0000-4000-8000-000000000001', '84000000-0000-4000-8000-000000000001',
   '83000000-0000-4000-8000-000000000001', '83000000-0000-4000-8000-000000000001',
   'asserts', '{"kind":"pdf_page","page_number":7}', 'orion original'),
  ('81000000-0000-4000-8000-000000000002', '87000000-0000-4000-8000-000000000002',
   '84000000-0000-4000-8000-000000000002', '84000000-0000-4000-8000-000000000002',
   '83000000-0000-4000-8000-000000000002', '83000000-0000-4000-8000-000000000002',
   'asserts', '{"kind":"pdf_page","page_number":9}', 'orion private');

set local role anon;
select throws_matching(
  $$select * from public.search_knowledge('orion', 20, 0)$$,
  'permission denied for function search_knowledge', 'anonymous invocation is denied'
);
reset role;
set local role authenticated;
set local request.jwt.claim.sub = '81000000-0000-4000-8000-000000000001';
select is((select count(*) from public.search_knowledge('orion', 20, 0)), 3::bigint, 'User A gets Note, Claim, and Evidence');
select set_eq(
  $$select result_type from public.search_knowledge('orion', 20, 0)$$,
  $$values ('source_note'::text), ('claim'::text), ('evidence'::text)$$,
  'result types are explicit'
);
select set_eq(
  $$select content_origin from public.search_knowledge('orion', 20, 0)$$,
  $$values ('generated_note'::text), ('generated_claim'::text), ('original_source'::text)$$,
  'generated and original content origins are distinct'
);
select ok((select bool_and(asset_id = '82000000-0000-4000-8000-000000000001') from public.search_knowledge('orion', 20, 0)), 'User A results use only owned Asset');
select ok((select bool_and(revision_id = '83000000-0000-4000-8000-000000000001') from public.search_knowledge('orion', 20, 0)), 'results identify immutable Revision');
select ok((select bool_and(jsonb_array_length(citations) > 0) from public.search_knowledge('orion', 20, 0)), 'every result includes citations');
select ok((select bool_and((citations -> 0 ->> 'evidenceId')::uuid = '84000000-0000-4000-8000-000000000001') from public.search_knowledge('orion', 20, 0)), 'citations use only owned Evidence');
select ok((select bool_and((citations -> 0 #>> '{locator,page_number}')::integer = 7) from public.search_knowledge('orion', 20, 0)), 'citations retain PDF page');
select is((select content_origin from public.search_knowledge('truth', 20, 0)), 'original_source', 'source-only term is labeled original');
select is((select content_origin from public.search_knowledge('analysis', 20, 0)), 'generated_note', 'Note-only term is labeled generated');
select is((select content_origin from public.search_knowledge('claim', 20, 0)), 'generated_claim', 'Claim-only term is labeled generated');
select ok((select bool_and(total_count = 3) from public.search_knowledge('orion', 20, 0)), 'total count is stable');
select is((select count(*) from public.search_knowledge('orion', 2, 0)), 2::bigint, 'limit is enforced');
select is((select count(*) from public.search_knowledge('orion', 20, 2)), 1::bigint, 'offset is enforced');
select is((select count(*) from public.search_knowledge('absentterm', 20, 0)), 0::bigint, 'no match returns empty set');
select throws_matching($$select * from public.search_knowledge('x', 20, 0)$$, 'retrieval parameters are invalid', 'short query is rejected');
select throws_matching($$select * from public.search_knowledge('orion', 51, 0)$$, 'retrieval parameters are invalid', 'oversized limit is rejected');

set local request.jwt.claim.sub = '81000000-0000-4000-8000-000000000002';
select is((select count(*) from public.search_knowledge('orion', 20, 0)), 3::bigint, 'User B sees own results');
select ok((select bool_and(asset_id = '82000000-0000-4000-8000-000000000002') from public.search_knowledge('orion', 20, 0)), 'User B cannot receive User A Asset');
select ok((select bool_and((citations -> 0 ->> 'evidenceId')::uuid = '84000000-0000-4000-8000-000000000002') from public.search_knowledge('orion', 20, 0)), 'User B cannot receive User A Evidence');

reset role;
select * from finish();
rollback;
