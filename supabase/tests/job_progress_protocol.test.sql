begin;
create extension if not exists pgtap with schema extensions;
set local search_path=public,extensions;
select plan(13);

select has_column('public','jobs','progress_percent','jobs stores progress percent');
select has_column('public','jobs','progress_stage','jobs stores progress stage');
select has_column('public','jobs','progress_updated_at','jobs stores progress timestamp');
select ok(not has_function_privilege('anon','public.report_job_progress(uuid,text,bigint,integer,text)','execute'),'anon cannot report Worker progress');
select ok(not has_function_privilege('authenticated','public.report_job_progress(uuid,text,bigint,integer,text)','execute'),'authenticated cannot report Worker progress');
select ok(has_function_privilege('service_role','public.report_job_progress(uuid,text,bigint,integer,text)','execute'),'service role can report Worker progress');

insert into auth.users(instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,raw_app_meta_data,raw_user_meta_data,created_at,updated_at)
values('00000000-0000-0000-0000-000000000000','86000000-0000-4000-8000-000000000001','authenticated','authenticated','progress@example.test','',now(),'{}','{}',now(),now());
insert into public.assets(id,user_id,source_type,status)
values('86000000-0000-4000-8000-000000000002','86000000-0000-4000-8000-000000000001','pdf','pending');
insert into public.jobs(id,user_id,asset_id,job_type,payload_json,idempotency_key)
values(
  '86000000-0000-4000-8000-000000000003',
  '86000000-0000-4000-8000-000000000001',
  '86000000-0000-4000-8000-000000000002',
  'ingest_pdf',
  jsonb_build_object(
    'schemaVersion',1,
    'jobId','86000000-0000-4000-8000-000000000003',
    'userId','86000000-0000-4000-8000-000000000001',
    'jobType','ingest_pdf',
    'idempotencyKey','progress-protocol',
    'input',jsonb_build_object('assetId','86000000-0000-4000-8000-000000000002')
  ),
  'progress-protocol'
);

set local role service_role;
select is((select status from public.claim_job('progress-worker','lightweight',120)),'running','Worker claims test Job');
select is((select progress_percent from public.jobs where id='86000000-0000-4000-8000-000000000003'),1::smallint,'claim initializes truthful nonzero progress');
select ok(public.report_job_progress('86000000-0000-4000-8000-000000000003','progress-worker',1,40,'transcribing_audio'),'valid fenced progress update succeeds');
select is((select progress_stage from public.jobs where id='86000000-0000-4000-8000-000000000003'),'transcribing_audio','progress stage is stored');
select ok(not public.report_job_progress('86000000-0000-4000-8000-000000000003','progress-worker',1,30,'downloading_media'),'progress cannot move backwards');
select ok(not public.report_job_progress('86000000-0000-4000-8000-000000000003','progress-worker',2,50,'preparing_media'),'wrong lease generation cannot update progress');
update public.jobs set status='completed',lease_owner=null,lease_expires_at=null where id='86000000-0000-4000-8000-000000000003';
select is((select progress_percent from public.jobs where id='86000000-0000-4000-8000-000000000003'),100::smallint,'completion trigger sets progress to 100');

select * from finish();
rollback;
