begin;
create extension if not exists pgtap with schema extensions;
set local search_path=public,extensions;
select plan(29);

select ok(not has_function_privilege('anon','public.create_instagram_reel_asset(text,text)','execute'),'anon cannot create Reel Assets');
select ok(has_function_privilege('authenticated','public.create_instagram_reel_asset(text,text)','execute'),'authenticated can create Reel Assets');
select ok(not has_function_privilege('anon','public.enqueue_instagram_reel_ingestion(uuid)','execute'),'anon cannot enqueue Reel ingestion');
select ok(has_function_privilege('authenticated','public.enqueue_instagram_reel_ingestion(uuid)','execute'),'authenticated can enqueue owned Reel ingestion');
select ok(not has_function_privilege('authenticated','public.publish_instagram_reel_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb,jsonb)','execute'),'authenticated cannot publish Reel ingestion');
select ok(has_function_privilege('service_role','public.publish_instagram_reel_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb,jsonb)','execute'),'service role can publish Reel ingestion');
select ok(not has_function_privilege('authenticated','public.get_instagram_reel_source_note_context(uuid,text,bigint)','execute'),'user cannot read Worker Source Note context');
select ok(has_function_privilege('service_role','public.get_instagram_reel_source_note_context(uuid,text,bigint)','execute'),'service role can read fenced Source Note context');
select ok(not has_function_privilege('authenticated','public.enqueue_instagram_reel_retrieval_index_jobs(text,text,integer)','execute'),'user cannot enqueue Reel retrieval directly');
select ok(has_function_privilege('service_role','public.enqueue_instagram_reel_retrieval_index_jobs(text,text,integer)','execute'),'service role can enqueue Reel retrieval');

insert into auth.users(instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,raw_app_meta_data,raw_user_meta_data,created_at,updated_at)
values('00000000-0000-0000-0000-000000000000','82000000-0000-4000-8000-000000000001','authenticated','authenticated','reel-a@example.test','',now(),'{}','{}',now(),now()),
      ('00000000-0000-0000-0000-000000000000','82000000-0000-4000-8000-000000000002','authenticated','authenticated','reel-b@example.test','',now(),'{}','{}',now(),now());
create temporary table reel_state(asset_id uuid,job_id uuid,revision_id uuid,source_note_job_id uuid,retrieval_job_id uuid,object_key text,media jsonb,segments jsonb);
grant select,insert,update on reel_state to authenticated,service_role;

set local role authenticated;
set local request.jwt.claim.sub='82000000-0000-4000-8000-000000000001';
select throws_matching($$select public.create_instagram_reel_asset('http://instagram.com/reel/C0dExReel1/','C0dExReel1')$$,'canonical Instagram Reel URL is invalid','non-canonical URL rejected');
insert into reel_state select (public.create_instagram_reel_asset('https://www.instagram.com/reel/C0dExReel1/','C0dExReel1')#>>'{asset,id}')::uuid,null;
select is((select source_type from public.assets where id=(select asset_id from reel_state)),'instagram_reel','creates Reel Asset');
select is((public.create_instagram_reel_asset('https://www.instagram.com/reel/C0dExReel1/','C0dExReel1')#>>'{asset,id}')::uuid,(select asset_id from reel_state),'deduplicates by canonical identity');
select throws_matching($$update public.assets set source_url='https://www.instagram.com/reel/Changed1/' where id=(select asset_id from reel_state)$$,'Instagram Reel Asset source identity is immutable','Reel identity is immutable');

reset role;
insert into storage.objects(bucket_id,name,owner_id,metadata)
select 'asset-private','82000000-0000-4000-8000-000000000001/'||asset_id||'/original/instagram-reel-contract.json','82000000-0000-4000-8000-000000000001','{"mimetype":"application/json"}'::jsonb from reel_state;
set local role authenticated;
set local request.jwt.claim.sub='82000000-0000-4000-8000-000000000001';
update reel_state set job_id=(public.enqueue_instagram_reel_ingestion(asset_id)->>'id')::uuid;
select is((select job_type from public.jobs where id=(select job_id from reel_state)),'ingest_instagram_reel','creates durable Reel Job without requiring keyframes');
reset role;
set local role authenticated;
set local request.jwt.claim.sub='82000000-0000-4000-8000-000000000002';
select is((select count(*) from public.assets where id=(select asset_id from reel_state)),0::bigint,'RLS hides Reel Asset from another user');

reset role;
update reel_state set
  object_key='82000000-0000-4000-8000-000000000001/'||asset_id||'/revisions/'||repeat('a',64)||'/instagram-reel-v1/instagram-reel.json',
  media=jsonb_build_array(jsonb_build_object('id','frame-001','kind','keyframe','object_key',
    '82000000-0000-4000-8000-000000000001/'||asset_id||'/revisions/'||repeat('a',64)||'/instagram-reel-v1/keyframes/frame-001',
    'sha256',repeat('b',64),'mime','image/jpeg','size_bytes',100)),
  segments=jsonb_build_array(
    jsonb_build_object('id','caption','text','Caption','content_hash',repeat('c',64),'evidence_locator',jsonb_build_object(
      'kind','instagram_caption','url','https://www.instagram.com/reel/C0dExReel1/','reel_id','C0dExReel1','start_offset',0,'end_offset',7,'source_hash',repeat('a',64))),
    jsonb_build_object('id','transcript-1','text','Transcript','content_hash',repeat('d',64),'evidence_locator',jsonb_build_object(
      'kind','video_segment','start_seconds',0,'end_seconds',4.2,'start_offset',9,'end_offset',19,'source_hash',repeat('a',64),'frame_ids',jsonb_build_array('frame-001'))));
set local role service_role;
select is((select status from public.claim_job('reel-worker','media',120)),'running','media Worker claims Reel Job');
select throws_matching($$select public.publish_instagram_reel_ingestion((select job_id from reel_state),'reel-worker',1,repeat('a',64),'instagram-reel-v1',(select object_key from reel_state),repeat('9',64),512,(select media from reel_state),jsonb_set((select segments from reel_state),'{1,evidence_locator,frame_ids,0}','"missing"'::jsonb))$$,'Instagram Reel video locator is invalid','unknown keyframe reference is rejected');
update reel_state set revision_id=(public.publish_instagram_reel_ingestion(job_id,'reel-worker',1,repeat('a',64),'instagram-reel-v1',object_key,repeat('9',64),512,media,segments)->>'revisionId')::uuid;
select is((select status from public.jobs where id=(select job_id from reel_state)),'completed','publication completes Reel Job');
select is((select status from public.assets where id=(select asset_id from reel_state)),'ready','publication makes Reel ready');
select is((select count(*) from public.asset_artifacts where revision_id=(select revision_id from reel_state)),2::bigint,'publication stores Contract and keyframe artifacts');
select is((select count(*) from public.asset_evidence where revision_id=(select revision_id from reel_state)),2::bigint,'publication stores caption and transcript Evidence');
select ok((select content_object_key like '%/keyframes/frame-001' from public.asset_evidence where revision_id=(select revision_id from reel_state) and locator_json->>'kind'='video_segment'),'video Evidence points to referenced keyframe');

reset role;
set local role authenticated;
set local request.jwt.claim.sub='82000000-0000-4000-8000-000000000001';
update reel_state set source_note_job_id=(public.enqueue_instagram_reel_source_note_generation(asset_id)->>'id')::uuid;
select is((select status from public.jobs where id=(select source_note_job_id from reel_state)),'queued','ready Reel enters Source Note queue');
reset role;
set local role service_role;
select is((select status from public.claim_job('reel-note-worker','llm',120)),'running','Source Note Worker claims Reel Job');
select is(public.get_instagram_reel_source_note_context((select source_note_job_id from reel_state),'reel-note-worker',1)->>'sourceType','instagram_reel','Source Note context identifies Reel');
select is(public.enqueue_instagram_reel_retrieval_index_jobs('test-model','test-v1',25),1,'ready Reel creates Retrieval Job');
update reel_state set retrieval_job_id=(select id from public.jobs where asset_id=reel_state.asset_id and job_type='index_retrieval' order by created_at desc limit 1);
select is((select status from public.claim_job('reel-retrieval-worker','retrieval',120)),'running','Retrieval Worker claims Reel Job');
select is(public.get_instagram_reel_retrieval_index_context((select retrieval_job_id from reel_state),'reel-retrieval-worker',1)->>'sourceType','instagram_reel','Retrieval context identifies Reel');

select * from finish();
rollback;
