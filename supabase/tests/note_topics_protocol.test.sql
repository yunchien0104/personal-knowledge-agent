begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(22);

select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.topics'::regclass), 'topics use forced RLS');
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.note_topics'::regclass), 'note topics use forced RLS');
select ok(not has_function_privilege('anon', 'public.set_note_topics(uuid,uuid[])', 'execute'), 'anon cannot edit topics');
select ok(not has_function_privilege('authenticated', 'public.apply_ai_note_topics(uuid,uuid[],text,text)', 'execute'), 'clients cannot apply AI results');
select ok(has_function_privilege('service_role', 'public.apply_ai_note_topics(uuid,uuid[],text,text)', 'execute'), 'worker can apply AI results');

insert into auth.users (instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,raw_app_meta_data,raw_user_meta_data,created_at,updated_at) values
('00000000-0000-0000-0000-000000000000','91000000-0000-4000-8000-000000000001','authenticated','authenticated','topic-a@example.test','',now(),'{}','{}',now(),now()),
('00000000-0000-0000-0000-000000000000','91000000-0000-4000-8000-000000000002','authenticated','authenticated','topic-b@example.test','',now(),'{}','{}',now(),now());

insert into public.assets (id,user_id,source_type,status) values
('92000000-0000-4000-8000-000000000001','91000000-0000-4000-8000-000000000001','pdf','source_removed');
insert into public.notes (id,user_id,note_type,title,body,publication_state,updated_at) values
('93000000-0000-4000-8000-000000000001','91000000-0000-4000-8000-000000000001','source_note','Old source','## Old **markdown**','draft','2026-01-01'),
('93000000-0000-4000-8000-000000000002','91000000-0000-4000-8000-000000000001','source_note','Current source','## Current **summary**','draft','2026-01-02'),
('93000000-0000-4000-8000-000000000003','91000000-0000-4000-8000-000000000001','research_note','Research','Draft body','draft','2026-01-03'),
('93000000-0000-4000-8000-000000000004','91000000-0000-4000-8000-000000000001','manual_note','Manual','Personal body','published','2026-01-04'),
('93000000-0000-4000-8000-000000000005','91000000-0000-4000-8000-000000000001','manual_note','Archived','Hidden','archived','2026-01-05'),
('93000000-0000-4000-8000-000000000006','91000000-0000-4000-8000-000000000002','manual_note','Other user','Private','published','2026-01-06');
insert into public.source_note_generations (id,user_id,note_id,asset_id,asset_id_snapshot,source_revision_id_snapshot,source_fingerprint,job_id,model_provider,model_name,prompt_version,output_hash,created_at) values
('94000000-0000-4000-8000-000000000001','91000000-0000-4000-8000-000000000001','93000000-0000-4000-8000-000000000001','92000000-0000-4000-8000-000000000001','92000000-0000-4000-8000-000000000001','95000000-0000-4000-8000-000000000001',repeat('a',64),'96000000-0000-4000-8000-000000000001','mock','mock','v1',repeat('b',64),'2026-01-01'),
('94000000-0000-4000-8000-000000000002','91000000-0000-4000-8000-000000000001','93000000-0000-4000-8000-000000000002','92000000-0000-4000-8000-000000000001','92000000-0000-4000-8000-000000000001','95000000-0000-4000-8000-000000000002',repeat('c',64),'96000000-0000-4000-8000-000000000002','mock','mock','v2',repeat('d',64),'2026-01-02');

set local role authenticated;
set local request.jwt.claim.sub = '91000000-0000-4000-8000-000000000001';
select is((select count(*) from public.ensure_default_topics()), 8::bigint, 'defaults are created per user');
select is((select count(*) from public.ensure_default_topics()), 8::bigint, 'default creation is idempotent');
insert into public.topics (id,user_id,name,description) values
('97000000-0000-4000-8000-000000000001','91000000-0000-4000-8000-000000000001','Custom one','first'),
('97000000-0000-4000-8000-000000000002','91000000-0000-4000-8000-000000000001','Custom two','second');
select is((public.set_note_topics('93000000-0000-4000-8000-000000000002',array['97000000-0000-4000-8000-000000000001'::uuid])->>'manualOverride')::boolean,true,'manual selection is authoritative');
select is((select assignment_source from public.note_topics where note_id='93000000-0000-4000-8000-000000000002'),'user','manual assignment source is stored');
select lives_ok($$select public.set_note_topics('93000000-0000-4000-8000-000000000002','{}'::uuid[])$$,'manual clear is accepted');
select is((select manual_override from public.note_topic_states where note_id='93000000-0000-4000-8000-000000000002'),true,'manual clear keeps override');

reset role;
set local role service_role;
select lives_ok($$select public.apply_ai_note_topics('93000000-0000-4000-8000-000000000002',array['97000000-0000-4000-8000-000000000002'::uuid],'classified',null)$$,'late AI result is recorded');
select is((select count(*) from public.note_topics where note_id='93000000-0000-4000-8000-000000000002'),0::bigint,'late AI cannot overwrite manual clear');
select is((select ai_suggested_topic_ids[1] from public.note_topic_states where note_id='93000000-0000-4000-8000-000000000002'),'97000000-0000-4000-8000-000000000002'::uuid,'AI suggestion is retained separately');
select lives_ok($$select public.apply_ai_note_topics('93000000-0000-4000-8000-000000000004',array['ffffffff-ffff-4fff-8fff-ffffffffffff'::uuid],'classified',null)$$,'invalid AI ids do not fail the note');
select is((select classification_status from public.note_topic_states where note_id='93000000-0000-4000-8000-000000000004'),'failed','invalid AI ids become failed classification');

reset role;
set local role authenticated;
set local request.jwt.claim.sub = '91000000-0000-4000-8000-000000000001';
select is((select count(*) from public.list_knowledge_notes(null,null,false,20,0)),2::bigint,'knowledge list contains current non-research notes only');
select is((select count(*) from public.list_knowledge_notes('Research',null,false,20,0)),0::bigint,'research notes stay in the research workspace');
select is((select source_status from public.list_knowledge_notes('Current',null,false,20,0)),'source_removed','removed source note remains with status');
select is((select count(*) from public.list_knowledge_notes(null,null,true,20,0)),2::bigint,'unclassified count uses same effective set');
select is((select count(*) from public.list_knowledge_notes('Archived',null,false,20,0)),0::bigint,'archived notes are excluded');
select throws_matching($$select public.set_note_topics('93000000-0000-4000-8000-000000000006','{}'::uuid[])$$,'owned note not found','cross-account note edit is denied');

reset role;
select * from finish();
rollback;
