begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(26);

select has_table('public', 'retrieval_chunks', 'retrieval chunk table exists');
select ok((select relrowsecurity and relforcerowsecurity from pg_class
           where oid = 'public.retrieval_chunks'::regclass), 'retrieval chunks force RLS');
select ok(has_function_privilege('authenticated',
  'public.hybrid_retrieve(text,extensions.vector,text,text,uuid[],integer,integer)',
  'execute'), 'authenticated user can call scoped hybrid retrieval');
select ok(not has_function_privilege('anon',
  'public.hybrid_retrieve(text,extensions.vector,text,text,uuid[],integer,integer)',
  'execute'), 'anonymous retrieval is denied');
select ok(not has_function_privilege('authenticated',
  'public.hybrid_retrieve_for_worker(uuid,text,extensions.vector,text,text,uuid[],integer,integer)',
  'execute'), 'worker tenant parameter is not client accessible');
select ok(not has_function_privilege('service_role',
  'public.hybrid_retrieve_for_user(uuid,text,extensions.vector,text,text,uuid[],integer,integer)',
  'execute'), 'private retrieval core has no Data API grant');
select is((select count(*) from pg_indexes where schemaname = 'public'
  and tablename = 'retrieval_chunks' and indexdef ~* '(hnsw|ivfflat)'), 0::bigint,
  'R1 uses exact vector search without an ANN index');

create function pg_temp.test_vector(p_axis integer)
returns extensions.vector(1536)
language sql immutable
as $$
  select ('[' || string_agg(case when i = p_axis then '1' else '0' end, ',') || ']')
    ::extensions.vector(1536)
  from generate_series(1, 1536) i
$$;

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values
  ('00000000-0000-0000-0000-000000000000', 'd1000000-0000-4000-8000-000000000001',
   'authenticated', 'authenticated', 'hybrid-a@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now()),
  ('00000000-0000-0000-0000-000000000000', 'd1000000-0000-4000-8000-000000000002',
   'authenticated', 'authenticated', 'hybrid-b@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now());

insert into public.assets (id, user_id, source_type, status) values
  ('d2000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001', 'pdf', 'ready'),
  ('d2000000-0000-4000-8000-000000000002', 'd1000000-0000-4000-8000-000000000001', 'pdf', 'ready'),
  ('d2000000-0000-4000-8000-000000000003', 'd1000000-0000-4000-8000-000000000001', 'pdf', 'deleting'),
  ('d2000000-0000-4000-8000-000000000004', 'd1000000-0000-4000-8000-000000000002', 'pdf', 'ready');
insert into public.asset_revisions (
  id, user_id, asset_id, revision_no, fingerprint, processor_version, status,
  normalized_object_key
) values
  ('d3000000-0000-4000-8000-000000000001', 'd1000000-0000-4000-8000-000000000001',
   'd2000000-0000-4000-8000-000000000001', 1, repeat('1',64), 'test-v1', 'validated',
   'd1000000-0000-4000-8000-000000000001/d2000000-0000-4000-8000-000000000001/old.json'),
  ('d3000000-0000-4000-8000-000000000002', 'd1000000-0000-4000-8000-000000000001',
   'd2000000-0000-4000-8000-000000000001', 2, repeat('2',64), 'test-v1', 'validated',
   'd1000000-0000-4000-8000-000000000001/d2000000-0000-4000-8000-000000000001/current.json'),
  ('d3000000-0000-4000-8000-000000000003', 'd1000000-0000-4000-8000-000000000001',
   'd2000000-0000-4000-8000-000000000002', 1, repeat('3',64), 'test-v1', 'validated',
   'd1000000-0000-4000-8000-000000000001/d2000000-0000-4000-8000-000000000002/current.json'),
  ('d3000000-0000-4000-8000-000000000004', 'd1000000-0000-4000-8000-000000000001',
   'd2000000-0000-4000-8000-000000000003', 1, repeat('4',64), 'test-v1', 'validated',
   'd1000000-0000-4000-8000-000000000001/d2000000-0000-4000-8000-000000000003/current.json'),
  ('d3000000-0000-4000-8000-000000000005', 'd1000000-0000-4000-8000-000000000002',
   'd2000000-0000-4000-8000-000000000004', 1, repeat('5',64), 'test-v1', 'validated',
   'd1000000-0000-4000-8000-000000000002/d2000000-0000-4000-8000-000000000004/current.json');
update public.assets set active_revision_id = case id
  when 'd2000000-0000-4000-8000-000000000001' then 'd3000000-0000-4000-8000-000000000002'::uuid
  when 'd2000000-0000-4000-8000-000000000002' then 'd3000000-0000-4000-8000-000000000003'::uuid
  when 'd2000000-0000-4000-8000-000000000003' then 'd3000000-0000-4000-8000-000000000004'::uuid
  else 'd3000000-0000-4000-8000-000000000005'::uuid end;

insert into public.asset_evidence (
  id, user_id, revision_id, locator_type, locator_json, excerpt, source_hash
) values
  ('d4000000-0000-4000-8000-000000000001','d1000000-0000-4000-8000-000000000001','d3000000-0000-4000-8000-000000000001','pdf','{"kind":"pdf_page","page_number":1}','legacy forbidden evidence',repeat('1',64)),
  ('d4000000-0000-4000-8000-000000000002','d1000000-0000-4000-8000-000000000001','d3000000-0000-4000-8000-000000000002','pdf','{"kind":"pdf_page","page_number":2}','renewable infrastructure expands grid capacity',repeat('2',64)),
  ('d4000000-0000-4000-8000-000000000003','d1000000-0000-4000-8000-000000000001','d3000000-0000-4000-8000-000000000003','pdf','{"kind":"pdf_page","page_number":3}','AI data centers increase grid load and create energy storage opportunities',repeat('3',64)),
  ('d4000000-0000-4000-8000-000000000004','d1000000-0000-4000-8000-000000000001','d3000000-0000-4000-8000-000000000004','pdf','{"kind":"pdf_page","page_number":4}','deleting source must not be retrieved',repeat('4',64)),
  ('d4000000-0000-4000-8000-000000000005','d1000000-0000-4000-8000-000000000002','d3000000-0000-4000-8000-000000000005','pdf','{"kind":"pdf_page","page_number":5}','renewable infrastructure private tenant secret',repeat('5',64));

insert into public.retrieval_chunks (
  id,user_id,asset_id,revision_id,evidence_id,chunk_index,content,content_hash,
  start_offset,end_offset,locator_json,embedding,embedding_model,embedding_version,embedding_status
) values
  ('d5000000-0000-4000-8000-000000000001','d1000000-0000-4000-8000-000000000001','d2000000-0000-4000-8000-000000000001','d3000000-0000-4000-8000-000000000001','d4000000-0000-4000-8000-000000000001',0,'legacy forbidden evidence',repeat('a',64),0,25,'{"kind":"pdf_page","page_number":1}',pg_temp.test_vector(1),'test-multilingual','v1','ready'),
  ('d5000000-0000-4000-8000-000000000002','d1000000-0000-4000-8000-000000000001','d2000000-0000-4000-8000-000000000001','d3000000-0000-4000-8000-000000000002','d4000000-0000-4000-8000-000000000002',0,'renewable infrastructure expands grid capacity',repeat('b',64),0,46,'{"kind":"pdf_page","page_number":2}',pg_temp.test_vector(1),'test-multilingual','v1','ready'),
  ('d5000000-0000-4000-8000-000000000003','d1000000-0000-4000-8000-000000000001','d2000000-0000-4000-8000-000000000002','d3000000-0000-4000-8000-000000000003','d4000000-0000-4000-8000-000000000003',0,'AI data centers increase grid load and create energy storage opportunities',repeat('c',64),0,66,'{"kind":"pdf_page","page_number":3}',pg_temp.test_vector(2),'test-multilingual','v1','ready'),
  ('d5000000-0000-4000-8000-000000000004','d1000000-0000-4000-8000-000000000001','d2000000-0000-4000-8000-000000000003','d3000000-0000-4000-8000-000000000004','d4000000-0000-4000-8000-000000000004',0,'deleting source must not be retrieved',repeat('d',64),0,37,'{"kind":"pdf_page","page_number":4}',pg_temp.test_vector(1),'test-multilingual','v1','ready'),
  ('d5000000-0000-4000-8000-000000000005','d1000000-0000-4000-8000-000000000002','d2000000-0000-4000-8000-000000000004','d3000000-0000-4000-8000-000000000005','d4000000-0000-4000-8000-000000000005',0,'renewable infrastructure private tenant secret',repeat('e',64),0,46,'{"kind":"pdf_page","page_number":5}',pg_temp.test_vector(1),'test-multilingual','v1','ready');

select throws_matching(
  $$insert into public.retrieval_chunks (
    id,user_id,asset_id,revision_id,evidence_id,chunk_index,content,content_hash,
    start_offset,end_offset,locator_json,embedding,embedding_model,embedding_version,embedding_status
  ) values (
    'd5000000-0000-4000-8000-000000000099','d1000000-0000-4000-8000-000000000001',
    'd2000000-0000-4000-8000-000000000001','d3000000-0000-4000-8000-000000000002',
    'd4000000-0000-4000-8000-000000000005',99,'cross tenant',repeat('f',64),0,12,
    '{"kind":"pdf_page","page_number":99}',pg_temp.test_vector(1),
    'test-multilingual','v1','ready')$$,
  'retrieval chunk provenance is inconsistent',
  'provenance trigger rejects cross-tenant Evidence even with valid UUID foreign keys');

set local role authenticated;
set local request.jwt.claim.sub = 'd1000000-0000-4000-8000-000000000001';
select ok(jsonb_array_length(public.hybrid_retrieve(
  'renewable capacity', null, 'test-multilingual', 'v1', null, 5, 20)->'results') = 1, 'dynamic OR lexical search recalls English keywords without full-sentence AND');
select is(public.hybrid_retrieve(
  'renewable capacity', null, 'test-multilingual', 'v1', null, 5, 20)
  #>> '{results,0,evidenceId}', 'd4000000-0000-4000-8000-000000000002',
  'lexical result preserves original Evidence identity');
select ok(jsonb_array_length(public.hybrid_retrieve(
  'quantum bananas', null, 'test-multilingual', 'v1', null, 5, 20)->'results') = 0, 'no unrelated whole-library fallback is used');
select is(public.hybrid_retrieve(
  '請摘要這份 PDF 對 AI 基礎建設機會的觀察', pg_temp.test_vector(2),
  'test-multilingual', 'v1', array['d2000000-0000-4000-8000-000000000002'::uuid], 1, 20)
  #>> '{results,0,evidenceId}', 'd4000000-0000-4000-8000-000000000003',
  'Chinese summary-style question recalls English Evidence semantically within one PDF');
select is(public.hybrid_retrieve(
  '請整理跨來源觀察', pg_temp.test_vector(2), 'test-multilingual', 'v1',
  array['d2000000-0000-4000-8000-000000000001'::uuid,
        'd2000000-0000-4000-8000-000000000002'::uuid], 1, 20)
  #>> '{results,0,assetId}', 'd2000000-0000-4000-8000-000000000002',
  'multiple-PDF scope is enforced and ranked');
select is(public.hybrid_retrieve(
  '請整理我的知識庫', pg_temp.test_vector(2), 'test-multilingual', 'v1', null, 1, 20)
  #>> '{results,0,assetId}', 'd2000000-0000-4000-8000-000000000002',
  'whole owned knowledge-base scope remains tenant constrained');
select is(public.hybrid_retrieve(
  'unrelated semantic query', pg_temp.test_vector(1), 'test-multilingual', 'v1', null, 1, 20)
  ->> 'sufficiency', 'unassessed', 'vector similarity never asserts evidence sufficiency');
select is(public.hybrid_retrieve(
  'renewable', null, 'test-multilingual', 'v1', null, 5, 20)
  ->> 'semanticStatus', 'degraded_embedding_unavailable',
  'embedding failure/degraded lexical mode is explicit');
select ok(jsonb_array_length(public.hybrid_retrieve(
  'legacy forbidden', null, 'test-multilingual', 'v1', null, 5, 20)->'results') = 0, 'old inactive revision chunks cannot be recalled');
select ok(jsonb_array_length(public.hybrid_retrieve(
  'deleting source', null, 'test-multilingual', 'v1', null, 5, 20)->'results') = 0, 'deleting sources cannot be recalled');
select ok(jsonb_array_length(public.hybrid_retrieve(
  'renewable', pg_temp.test_vector(1), 'different-model', 'v1', null, 5, 20)->'results') = 0, 'incompatible embedding model is excluded');
select throws_matching(
  $$select public.hybrid_retrieve('query', null, 'test-multilingual', 'v1',
    array['d2000000-0000-4000-8000-000000000004'::uuid], 5, 20)$$,
  'retrieval scope contains unavailable asset', 'caller cannot expand scope to another tenant');
select throws_matching(
  $$select public.hybrid_retrieve(repeat('x',501), null, 'test-multilingual', 'v1', null, 5, 20)$$,
  'hybrid retrieval request is invalid', 'oversized query fails closed');
select throws_matching(
  $$select public.hybrid_retrieve('query', null, 'test-multilingual', 'v1', null, 0, 20)$$,
  'hybrid retrieval request is invalid', 'invalid Top-K fails closed');
select throws_matching(
  $$select public.hybrid_retrieve('query', null, 'test-multilingual', 'v1', array[]::uuid[], 5, 20)$$,
  'hybrid retrieval request is invalid', 'empty explicit scope is rejected rather than expanded');

reset role;
set local role authenticated;
set local request.jwt.claim.sub = 'd1000000-0000-4000-8000-000000000002';
select is((select count(*) from public.retrieval_chunks), 1::bigint,
  'RLS exposes only the current tenant retrieval rows');
select ok(jsonb_array_length(public.hybrid_retrieve(
  'renewable infrastructure', null, 'test-multilingual', 'v1', null, 5, 20)->'results') = 1, 'second tenant retrieves only its own matching Evidence');
select is(public.hybrid_retrieve(
  'renewable infrastructure', null, 'test-multilingual', 'v1', null, 5, 20)
  #>> '{results,0,evidenceId}', 'd4000000-0000-4000-8000-000000000005',
  'cross-user Evidence is never mixed');

reset role;
select * from finish();
rollback;
