begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(26);

select ok(not has_function_privilege('anon', 'public.create_threads_asset_and_enqueue(text,text,text)', 'execute'), 'anon cannot submit Threads URLs');
select ok(has_function_privilege('authenticated', 'public.create_threads_asset_and_enqueue(text,text,text)', 'execute'), 'authenticated users can submit Threads URLs');
select ok(not has_function_privilege('authenticated', 'public.publish_threads_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb)', 'execute'), 'authenticated users cannot publish worker output');
select ok(has_function_privilege('service_role', 'public.publish_threads_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb)', 'execute'), 'service role can publish worker output');

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values (
  '00000000-0000-0000-0000-000000000000',
  '81000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated',
  'threads-ingestion@example.test', '', now(),
  '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now()
), (
  '00000000-0000-0000-0000-000000000000',
  '81000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated',
  'threads-isolation@example.test', '', now(),
  '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now()
);

create temporary table threads_state (
  asset_id uuid, job_id uuid, revision_id uuid, object_key text, segments jsonb
);
grant select, update, insert on threads_state to authenticated, service_role;

set local role authenticated;
set local request.jwt.claim.sub = '81000000-0000-4000-8000-000000000001';
with result as (
  select public.create_threads_asset_and_enqueue(
    'https://www.threads.net/t/DewcDm5n4FD', 'DewcDm5n4FD', 'User supplied Evidence'
  ) value
)
insert into threads_state (asset_id, job_id, object_key, segments)
select
  (value #>> '{asset,id}')::uuid,
  (value #>> '{job,id}')::uuid,
  '81000000-0000-4000-8000-000000000001/' || (value #>> '{asset,id}')
    || '/revisions/' || repeat('a', 64) || '/threads-playwright-v1/threads-post.json',
  jsonb_build_array(jsonb_build_object(
    'id', '0_22', 'text', 'User supplied Evidence', 'content_hash', repeat('b', 64),
    'evidence_locator', jsonb_build_object(
      'kind', 'threads_post', 'url', 'https://www.threads.net/t/DewcDm5n4FD',
      'post_id', 'DewcDm5n4FD', 'start_offset', 0, 'end_offset', 22,
      'source_hash', repeat('a', 64)
    )
  ))
from result;

select ok((select asset_id is not null and job_id is not null from threads_state), 'submission atomically creates Asset and Job');
select is((select payload_json #>> '{input,providedText}' from public.jobs where id = (select job_id from threads_state)), 'User supplied Evidence', 'fallback text is carried in the typed Job payload');
select is((select source_url from public.assets where id = (select asset_id from threads_state)), 'https://www.threads.net/t/DewcDm5n4FD', 'Asset retains canonical source URL');
select is((select status from public.jobs where id = (select job_id from threads_state)), 'queued', 'Threads ingestion is queued');
select ok((public.create_threads_asset_and_enqueue('https://www.threads.net/t/DewcDm5n4FD', 'DewcDm5n4FD', null) ->> 'duplicate')::boolean, 'duplicate submission is reported');
select is((select count(*) from public.assets where user_id = '81000000-0000-4000-8000-000000000001' and source_type = 'threads'), 1::bigint, 'duplicate creates no second Asset');

reset role;
set local role service_role;
update public.jobs set status = 'failed' where id = (select job_id from threads_state);
update public.assets set status = 'failed' where id = (select asset_id from threads_state);

reset role;
set local role authenticated;
set local request.jwt.claim.sub = '81000000-0000-4000-8000-000000000001';
select ok(not (public.create_threads_asset_and_enqueue(
  'https://www.threads.net/@author/post/DewcDm5n4FD', 'DewcDm5n4FD', null
) ->> 'duplicate')::boolean, 'failed import can be resubmitted with the account-qualified URL');
select is((select source_url from public.assets where id = (select asset_id from threads_state)), 'https://www.threads.net/@author/post/DewcDm5n4FD', 'retry preserves the fetchable account-qualified URL');
select is((select payload_json #>> '{input,sourceUrl}' from public.jobs where id = (select job_id from threads_state)), 'https://www.threads.net/@author/post/DewcDm5n4FD', 'retry updates the typed Job source URL');
update threads_state set segments = jsonb_set(
  segments,
  '{0,evidence_locator,url}',
  to_jsonb('https://www.threads.net/@author/post/DewcDm5n4FD'::text)
);
select throws_ok(
  $$update public.assets set source_url = 'https://www.threads.net/t/OtherPost1'
    where id = (select asset_id from threads_state)$$,
  '23514', 'Threads Asset source identity is immutable',
  'owner cannot retarget a Threads Asset URL'
);

reset role;
set local role service_role;
select is((select status from public.claim_job('threads-worker', 'media', 120)), 'running', 'media worker claims Threads Job');
update threads_state set revision_id = (
  public.publish_threads_ingestion(
    job_id, 'threads-worker', 1, repeat('a', 64), 'threads-playwright-v1',
    object_key, repeat('d', 64), 512, segments
  ) ->> 'revisionId'
)::uuid;
select is((select status from public.jobs where id = (select job_id from threads_state)), 'completed', 'fenced publication completes Job');
select is((select status from public.assets where id = (select asset_id from threads_state)), 'ready', 'fenced publication promotes Asset');
select is((select count(*) from public.asset_artifacts where revision_id = (select revision_id from threads_state) and kind = 'threads_post_text'), 1::bigint, 'publication records normalized Artifact');
select is((select count(*) from public.asset_evidence where revision_id = (select revision_id from threads_state) and locator_type = 'post'), 1::bigint, 'publication records citation Evidence');

reset role;
set local role authenticated;
set local request.jwt.claim.sub = '81000000-0000-4000-8000-000000000001';
select is((public.enqueue_source_note_generation((select asset_id from threads_state)) ->> 'status'), 'queued', 'Threads uses the Source Note queue');

reset role;
set local role service_role;
update public.jobs set next_run_at = now() + interval '1 day'
where job_type = 'generate_source_note'
  and asset_id <> (select asset_id from threads_state)
  and status in ('queued', 'retry_wait');
select is((select status from public.claim_job('source-note-worker', 'llm', 120)), 'running', 'Source Note worker claims Threads source');
select is(public.get_source_note_context((select id from public.jobs where job_type = 'generate_source_note' and asset_id = (select asset_id from threads_state)), 'source-note-worker', 1) ->> 'sourceType', 'threads', 'Source Note context identifies Threads provenance');
select cmp_ok(public.enqueue_retrieval_index_jobs('test-embedding', 'test-v1', 25), '>=', 1, 'ready Threads revision enters retrieval queue');
update public.jobs set next_run_at = now() + interval '1 day'
where job_type = 'index_retrieval'
  and asset_id <> (select asset_id from threads_state)
  and status in ('queued', 'retry_wait');
select is((select status from public.claim_job('retrieval-worker', 'retrieval', 120)), 'running', 'retrieval worker claims Threads source');
select is(public.get_retrieval_index_context((select id from public.jobs where job_type = 'index_retrieval' and asset_id = (select asset_id from threads_state)), 'retrieval-worker', 1) ->> 'sourceType', 'threads', 'retrieval context identifies Threads artifact');

reset role;
set local role authenticated;
set local request.jwt.claim.sub = '81000000-0000-4000-8000-000000000002';
select is((select count(*) from public.assets where id = (select asset_id from threads_state)), 0::bigint, 'another tenant cannot read the Threads Asset');

reset role;
select * from finish();
rollback;
