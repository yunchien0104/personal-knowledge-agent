begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(16);

insert into auth.users(instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,raw_app_meta_data,raw_user_meta_data,created_at,updated_at) values
('00000000-0000-0000-0000-000000000000','7a110000-0000-4000-8000-000000000001','authenticated','authenticated','reference-a@example.test','',now(),'{}','{}',now(),now()),
('00000000-0000-0000-0000-000000000000','7a110000-0000-4000-8000-000000000002','authenticated','authenticated','reference-b@example.test','',now(),'{}','{}',now(),now());

insert into public.assets(id,user_id,source_type,source_url,status,created_at) values
('a0000000-0000-4000-8000-000000000001','7a110000-0000-4000-8000-000000000001','pdf','https://example.test/a.pdf','ready','2026-01-01'),
('a0000000-0000-4000-8000-000000000002','7a110000-0000-4000-8000-000000000001','instagram_reel','https://example.test/reel','ready','2026-01-02'),
('a0000000-0000-4000-8000-000000000003','7a110000-0000-4000-8000-000000000002','pdf','https://example.test/private.pdf','ready','2026-01-03');
insert into public.asset_revisions(id,user_id,asset_id,revision_no,fingerprint,processor_version,status,created_at) values
('b0000000-0000-4000-8000-000000000001','7a110000-0000-4000-8000-000000000001','a0000000-0000-4000-8000-000000000001',1,repeat('1',64),'test','validated','2026-01-01'),
('b0000000-0000-4000-8000-000000000002','7a110000-0000-4000-8000-000000000001','a0000000-0000-4000-8000-000000000002',1,repeat('2',64),'test','validated','2026-01-02'),
('b0000000-0000-4000-8000-000000000003','7a110000-0000-4000-8000-000000000002','a0000000-0000-4000-8000-000000000003',1,repeat('3',64),'test','validated','2026-01-03');
update public.assets a set active_revision_id = r.id
from public.asset_revisions r where r.asset_id = a.id;

insert into public.notes(id,user_id,note_type,title,body,publication_state,created_at,updated_at) values
('c0000000-0000-4000-8000-000000000001','7a110000-0000-4000-8000-000000000001','source_note','PDF knowledge','PDF body','published','2026-02-03','2026-02-03'),
('c0000000-0000-4000-8000-000000000002','7a110000-0000-4000-8000-000000000001','source_note','Reel knowledge','Reel body','published','2026-02-02','2026-02-02'),
('c0000000-0000-4000-8000-000000000003','7a110000-0000-4000-8000-000000000002','source_note','Private knowledge','Private body','published','2026-02-04','2026-02-04'),
('d0000000-0000-4000-8000-000000000001','7a110000-0000-4000-8000-000000000001','research_note','Published report','Report body','published','2026-03-03','2026-03-03'),
('d0000000-0000-4000-8000-000000000002','7a110000-0000-4000-8000-000000000001','research_note','Draft report','Draft body','draft','2026-03-04','2026-03-04'),
('e0000000-0000-4000-8000-000000000001','7a110000-0000-4000-8000-000000000001','manual_note','Current note','Current','draft','2026-04-04','2026-04-04'),
('e0000000-0000-4000-8000-000000000002','7a110000-0000-4000-8000-000000000001','manual_note','Tie lower id','Lower','draft','2026-04-03','2026-11-01'),
('e0000000-0000-4000-8000-000000000003','7a110000-0000-4000-8000-000000000001','manual_note','Tie higher id','Higher','draft','2026-04-03','2026-04-03'),
('e0000000-0000-4000-8000-000000000004','7a110000-0000-4000-8000-000000000002','manual_note','Private note','Private','draft','2026-04-05','2026-04-05');

insert into public.source_note_generations(id,user_id,note_id,asset_id,source_revision_id,asset_id_snapshot,source_revision_id_snapshot,source_fingerprint,job_id,model_provider,model_name,prompt_version,output_hash,created_at) values
('f0000000-0000-4000-8000-000000000001','7a110000-0000-4000-8000-000000000001','c0000000-0000-4000-8000-000000000001','a0000000-0000-4000-8000-000000000001','b0000000-0000-4000-8000-000000000001','a0000000-0000-4000-8000-000000000001','b0000000-0000-4000-8000-000000000001',repeat('1',64),'f1000000-0000-4000-8000-000000000001','test','test','test-v1',repeat('a',64),'2026-02-03'),
('f0000000-0000-4000-8000-000000000002','7a110000-0000-4000-8000-000000000001','c0000000-0000-4000-8000-000000000002','a0000000-0000-4000-8000-000000000002','b0000000-0000-4000-8000-000000000002','a0000000-0000-4000-8000-000000000002','b0000000-0000-4000-8000-000000000002',repeat('2',64),'f1000000-0000-4000-8000-000000000002','test','test','test-v1',repeat('b',64),'2026-02-02'),
('f0000000-0000-4000-8000-000000000003','7a110000-0000-4000-8000-000000000002','c0000000-0000-4000-8000-000000000003','a0000000-0000-4000-8000-000000000003','b0000000-0000-4000-8000-000000000003','a0000000-0000-4000-8000-000000000003','b0000000-0000-4000-8000-000000000003',repeat('3',64),'f1000000-0000-4000-8000-000000000003','test','test','test-v1',repeat('c',64),'2026-02-04');

insert into public.research_runs(id,user_id,request_key,question,status,output_note_id,created_at) values
('91000000-0000-4000-8000-000000000001','7a110000-0000-4000-8000-000000000001','92000000-0000-4000-8000-000000000001','Published synthetic research?','draft_ready','d0000000-0000-4000-8000-000000000001','2026-03-01'),
('91000000-0000-4000-8000-000000000002','7a110000-0000-4000-8000-000000000001','92000000-0000-4000-8000-000000000002','Draft synthetic research?','draft_ready','d0000000-0000-4000-8000-000000000002','2026-03-02');

insert into public.personal_note_documents(note_id,user_id,content_json,plain_text) values
('e0000000-0000-4000-8000-000000000002','7a110000-0000-4000-8000-000000000001','{"type":"doc","content":[]}','Older updated note'),
('e0000000-0000-4000-8000-000000000003','7a110000-0000-4000-8000-000000000001','{"type":"doc","content":[]}','Higher id note');

set local role authenticated;
set local request.jwt.claim.sub='7a110000-0000-4000-8000-000000000001';

select throws_matching($$select * from public.list_reference_sources(null,null,null,null,20,0)$$,'invalid reference category','RPC rejects an invalid category');
select is((select count(*) from public.list_reference_sources('research',null,null,null,20,0)),1::bigint,'research contains only completed published reports');
select is((select category from public.list_reference_sources('research',null,null,null,20,0) limit 1),'research','research category is not remapped');
select is((select count(*) from public.list_reference_sources('knowledge',null,null,null,20,0)),2::bigint,'knowledge contains source notes only');
select is((select count(*) from public.list_reference_sources('personal',null,null,'e0000000-0000-4000-8000-000000000001',20,0)),2::bigint,'personal contains manual notes and excludes current note');
select is((select count(*) from public.list_reference_sources('knowledge','pdf',null,null,20,0)),1::bigint,'PDF metadata filter works');
select is((select count(*) from public.list_reference_sources('knowledge','instagram_reel',null,null,20,0)),1::bigint,'Instagram Reel metadata filter works');
select is((select id from public.list_reference_sources('knowledge',null,null,null,20,0) limit 1),'c0000000-0000-4000-8000-000000000001'::uuid,'knowledge sorts by source note created_at');
select is((select id from public.list_reference_sources('research',null,null,null,20,0) limit 1),'d0000000-0000-4000-8000-000000000001'::uuid,'research sorts by report created_at');
select is((select id from public.list_reference_sources('personal',null,null,'e0000000-0000-4000-8000-000000000001',20,0) limit 1),'e0000000-0000-4000-8000-000000000003'::uuid,'personal tie sorts by id desc, not updated_at');
select is((select count(*) from public.list_reference_sources('knowledge',null,'Reel',null,20,0)),1::bigint,'search combines with category');
select is((select count(*) from public.list_reference_sources('knowledge',null,null,null,1,1)),1::bigint,'server pagination returns second page');
select is((select total_count from public.list_reference_sources('knowledge',null,null,null,1,1)),2::bigint,'pagination preserves total count');
select is((select source_type from public.list_reference_sources('knowledge','pdf',null,null,20,0)),'pdf','source type comes from linked asset metadata');
select is((select count(*) from public.list_reference_sources('personal',null,'Private',null,20,0)),0::bigint,'RLS and ownership exclude another account');

insert into public.personal_note_imports(id,user_id,note_id,source_kind,source_note_id,snapshot_title,snapshot_text) values
('93000000-0000-4000-8000-000000000001','7a110000-0000-4000-8000-000000000001','e0000000-0000-4000-8000-000000000001','personal','e0000000-0000-4000-8000-000000000002','Tie lower id','Older updated note');
insert into public.personal_note_citations(user_id,note_id,import_id,source_label,supporting_quote,locator_json) values
('7a110000-0000-4000-8000-000000000001','e0000000-0000-4000-8000-000000000001','93000000-0000-4000-8000-000000000001','Tie lower id','Older updated note','{"category":"personal","operation":"full","sourceNoteId":"e0000000-0000-4000-8000-000000000002"}');
select is((select locator_json->>'category' from public.personal_note_citations where import_id='93000000-0000-4000-8000-000000000001'),'personal','import and citation retain source category metadata');

select * from finish();
rollback;
