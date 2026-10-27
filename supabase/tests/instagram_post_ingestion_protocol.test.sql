begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(39);

select ok(
  not has_function_privilege(
    'anon', 'public.create_instagram_post_asset(text,text)', 'execute'
  ),
  'anon cannot enqueue Instagram Post ingestion'
);
select ok(
  has_function_privilege(
    'authenticated', 'public.create_instagram_post_asset(text,text)', 'execute'
  ),
  'authenticated users can enqueue Instagram Post ingestion'
);
select ok(
  not has_function_privilege(
    'anon', 'public.enqueue_instagram_post_ingestion(uuid)', 'execute'
  ),
  'anon cannot finalize Instagram Post uploads'
);
select ok(
  has_function_privilege(
    'authenticated', 'public.enqueue_instagram_post_ingestion(uuid)', 'execute'
  ),
  'authenticated users can enqueue a verified Instagram Post upload'
);
select ok(
  not has_function_privilege(
    'anon',
    'public.publish_instagram_post_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb,jsonb)',
    'execute'
  ),
  'anon cannot publish Instagram Post ingestion'
);
select ok(
  not has_function_privilege(
    'authenticated',
    'public.publish_instagram_post_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb,jsonb)',
    'execute'
  ),
  'authenticated users cannot publish Instagram Post ingestion'
);
select ok(
  has_function_privilege(
    'service_role',
    'public.publish_instagram_post_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb,jsonb)',
    'execute'
  ),
  'service role can publish Instagram Post ingestion'
);
select ok(
  (
    select count(*) = 1
      and count(*) filter (
        where coalesce(granted_role.rolname, 'PUBLIC') = 'service_role'
      ) = 1
    from pg_proc as function
    cross join lateral aclexplode(
      coalesce(function.proacl, acldefault('f', function.proowner))
    ) as privilege
    left join pg_roles as granted_role on granted_role.oid = privilege.grantee
    where function.oid =
      'public.publish_instagram_post_ingestion(uuid,text,bigint,text,text,text,text,bigint,jsonb,jsonb)'::regprocedure
      and privilege.privilege_type = 'EXECUTE'
      and privilege.grantee <> function.proowner
  ),
  'publication RPC has only one explicit non-owner grant'
);
select ok(
  not has_function_privilege(
    'anon', 'public.enqueue_instagram_post_source_note_generation(uuid)', 'execute'
  ),
  'anon cannot enqueue an Instagram Post Source Note'
);
select ok(
  has_function_privilege(
    'authenticated', 'public.enqueue_instagram_post_source_note_generation(uuid)', 'execute'
  ),
  'authenticated users can enqueue an owned Instagram Post Source Note'
);
select ok(
  not has_function_privilege(
    'authenticated',
    'public.get_instagram_post_source_note_context(uuid,text,bigint)', 'execute'
  ),
  'authenticated users cannot read Worker Source Note context'
);
select ok(
  has_function_privilege(
    'service_role',
    'public.get_instagram_post_source_note_context(uuid,text,bigint)', 'execute'
  ),
  'service role can read fenced Instagram Post Source Note context'
);
select ok(
  not has_function_privilege(
    'authenticated',
    'public.enqueue_instagram_post_retrieval_index_jobs(text,text,integer)', 'execute'
  ),
  'authenticated users cannot enqueue retrieval indexing directly'
);
select ok(
  has_function_privilege(
    'service_role',
    'public.enqueue_instagram_post_retrieval_index_jobs(text,text,integer)', 'execute'
  ),
  'service role can enqueue Instagram Post retrieval indexing'
);

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values
  (
    '00000000-0000-0000-0000-000000000000',
    '81000000-0000-4000-8000-000000000001',
    'authenticated', 'authenticated', 'ig-post-a@example.test', '', now(),
    '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb,
    now(), now()
  ),
  (
    '00000000-0000-0000-0000-000000000000',
    '81000000-0000-4000-8000-000000000002',
    'authenticated', 'authenticated', 'ig-post-b@example.test', '', now(),
    '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb,
    now(), now()
  );

select lives_ok(
  $$insert into public.assets (user_id, source_type) values
    ('81000000-0000-4000-8000-000000000001', 'pdf'),
    ('81000000-0000-4000-8000-000000000001', 'threads')$$,
  'existing PDF and Threads source types remain accepted'
);
select throws_matching(
  $$insert into public.assets (user_id, source_type) values
    ('81000000-0000-4000-8000-000000000001', 'unknown_source')$$,
  'violates check constraint',
  'unknown source types remain rejected'
);

create temporary table instagram_post_state (
  asset_id uuid,
  job_id uuid,
  revision_id uuid,
  source_note_job_id uuid,
  retrieval_job_id uuid,
  object_key text,
  media jsonb,
  segments jsonb
);
grant select, insert, update on instagram_post_state to authenticated, service_role;

set local role authenticated;
set local request.jwt.claim.sub = '81000000-0000-4000-8000-000000000001';

select throws_matching(
  $$select public.create_instagram_post_asset(
    'http://instagram.com/p/C0dExPost1/', 'C0dExPost1'
  )$$,
  'canonical Instagram Post URL is invalid',
  'non-canonical Instagram Post URL is rejected'
);

insert into instagram_post_state (asset_id, job_id)
select (result #>> '{asset,id}')::uuid, null::uuid
from (
  select public.create_instagram_post_asset(
    'https://www.instagram.com/p/C0dExPost1/', 'C0dExPost1'
  ) as result
) as created;

select is(
  (select source_type from public.assets
   where id = (select asset_id from instagram_post_state)),
  'instagram_post',
  'authenticated enqueue creates an Instagram Post Asset'
);
select is(
  (
    public.create_instagram_post_asset(
      'https://www.instagram.com/p/C0dExPost1/', 'C0dExPost1'
    ) #>> '{asset,id}'
  )::uuid,
  (select asset_id from instagram_post_state),
  'repeated enqueue reuses the canonical Asset'
);
select throws_matching(
  $$update public.assets set source_url = 'https://www.instagram.com/p/Changed1/'
    where id = (select asset_id from instagram_post_state)$$,
  'Instagram Post Asset source identity is immutable',
  'an established Instagram Post identity cannot be rewritten'
);

reset role;
insert into storage.objects (bucket_id, name, owner_id, metadata)
select 'asset-private',
       '81000000-0000-4000-8000-000000000001/' || asset_id::text
         || '/original/instagram-contract.json',
       '81000000-0000-4000-8000-000000000001',
       '{"mimetype":"application/json"}'::jsonb
from instagram_post_state
union all
select 'asset-private',
       '81000000-0000-4000-8000-000000000001/' || asset_id::text
         || '/original/images/image-001',
       '81000000-0000-4000-8000-000000000001',
       '{"mimetype":"image/jpeg"}'::jsonb
from instagram_post_state
union all
select 'asset-private',
       '81000000-0000-4000-8000-000000000001/' || asset_id::text
         || '/original/images/image-002',
       '81000000-0000-4000-8000-000000000001',
       '{"mimetype":"image/jpeg"}'::jsonb
from instagram_post_state;

set local role authenticated;
set local request.jwt.claim.sub = '81000000-0000-4000-8000-000000000001';
update instagram_post_state
set job_id = (
  public.enqueue_instagram_post_ingestion(asset_id) ->> 'id'
)::uuid;
select is(
  (select job_type from public.jobs
   where id = (select job_id from instagram_post_state)),
  'ingest_instagram_post',
  'verified upload enqueue creates the expected durable Job'
);

reset role;
set local role authenticated;
set local request.jwt.claim.sub = '81000000-0000-4000-8000-000000000002';
select is(
  (select count(*) from public.assets
   where id = (select asset_id from instagram_post_state)),
  0::bigint,
  'another user cannot read the Instagram Post Asset'
);

reset role;
update instagram_post_state
set object_key =
      '81000000-0000-4000-8000-000000000001/' || asset_id::text
      || '/revisions/' || repeat('a', 64)
      || '/instagram-post-v1/instagram-post.json',
    media = jsonb_build_array(
      jsonb_build_object(
        'id', 'image-001', 'kind', 'image',
        'object_key',
          '81000000-0000-4000-8000-000000000001/' || asset_id::text
          || '/revisions/' || repeat('a', 64)
          || '/instagram-post-v1/images/image-001',
        'sha256', repeat('b', 64), 'mime', 'image/jpeg', 'size_bytes', 100
      ),
      jsonb_build_object(
        'id', 'image-002', 'kind', 'image',
        'object_key',
          '81000000-0000-4000-8000-000000000001/' || asset_id::text
          || '/revisions/' || repeat('a', 64)
          || '/instagram-post-v1/images/image-002',
        'sha256', repeat('c', 64), 'mime', 'image/jpeg', 'size_bytes', 200
      )
    ),
    segments = jsonb_build_array(
      jsonb_build_object(
        'id', 'caption', 'text', 'Caption', 'content_hash', repeat('d', 64),
        'evidence_locator', jsonb_build_object(
          'kind', 'instagram_post_caption',
          'url', 'https://www.instagram.com/p/C0dExPost1/',
          'post_id', 'C0dExPost1', 'start_offset', 0, 'end_offset', 7,
          'source_hash', repeat('a', 64)
        )
      ),
      jsonb_build_object(
        'id', 'image-001-ocr', 'text', 'Image one',
        'content_hash', repeat('e', 64),
        'artifact_refs', jsonb_build_array('image-001'),
        'evidence_locator', jsonb_build_object(
          'kind', 'instagram_image',
          'url', 'https://www.instagram.com/p/C0dExPost1/',
          'post_id', 'C0dExPost1', 'image_index', 1, 'media_id', 'image-001',
          'start_offset', 9, 'end_offset', 18, 'source_hash', repeat('a', 64)
        )
      ),
      jsonb_build_object(
        'id', 'image-002-ocr', 'text', 'Image two',
        'content_hash', repeat('f', 64),
        'artifact_refs', jsonb_build_array('image-002'),
        'evidence_locator', jsonb_build_object(
          'kind', 'instagram_image',
          'url', 'https://www.instagram.com/p/C0dExPost1/',
          'post_id', 'C0dExPost1', 'image_index', 2, 'media_id', 'image-002',
          'start_offset', 20, 'end_offset', 29, 'source_hash', repeat('a', 64)
        )
      )
    );

set local role service_role;
select is(
  (select status from public.claim_job('instagram-post-worker', 'media', 120)),
  'running',
  'media Worker claims the Instagram Post Job'
);

select throws_matching(
  $$select public.publish_instagram_post_ingestion(
    (select job_id from instagram_post_state), 'instagram-post-worker', 1,
    repeat('a', 64), 'instagram-post-v1',
    (select object_key from instagram_post_state), repeat('9', 64), 512,
    (select media from instagram_post_state),
    jsonb_set(
      (select segments from instagram_post_state),
      '{1,evidence_locator,media_id}', '"image-002"'::jsonb
    )
  )$$,
  'Instagram image locator is inconsistent',
  'an OCR locator cannot point at a different image'
);

update instagram_post_state
set revision_id = (
  public.publish_instagram_post_ingestion(
    job_id, 'instagram-post-worker', 1, repeat('a', 64),
    'instagram-post-v1', object_key, repeat('9', 64), 512, media, segments
  ) ->> 'revisionId'
)::uuid;

select is(
  (select status from public.jobs where id = (select job_id from instagram_post_state)),
  'completed',
  'valid publication completes the Job'
);
select is(
  (select status from public.assets where id = (select asset_id from instagram_post_state)),
  'ready',
  'valid publication makes the Asset ready'
);
select is(
  (select count(*) from public.asset_artifacts
   where revision_id = (select revision_id from instagram_post_state)),
  3::bigint,
  'publication stores one normalized artifact and two image artifacts'
);
select is(
  (select count(*) from public.asset_evidence
   where revision_id = (select revision_id from instagram_post_state)),
  3::bigint,
  'publication stores caption and ordered OCR Evidence'
);
select results_eq(
  $$select locator_json ->> 'kind'
    from public.asset_evidence
    where revision_id = (select revision_id from instagram_post_state)
    order by (locator_json ->> 'start_offset')::integer$$,
  $$values ('instagram_post_caption'), ('instagram_image'), ('instagram_image')$$,
  'Evidence retains the new Instagram Post locator kinds'
);
select is(
  (
    select content_object_key from public.asset_evidence
    where revision_id = (select revision_id from instagram_post_state)
      and locator_json ->> 'media_id' = 'image-001'
  ),
  (
    select media -> 0 ->> 'object_key' from instagram_post_state
  ),
  'image OCR Evidence points to the matching private image artifact'
);
select is(
  (
    public.publish_instagram_post_ingestion(
      (select job_id from instagram_post_state), 'instagram-post-worker', 1,
      repeat('a', 64), 'instagram-post-v1',
      (select object_key from instagram_post_state), repeat('9', 64), 512,
      (select media from instagram_post_state),
      (select segments from instagram_post_state)
    ) ->> 'revisionId'
  )::uuid,
  (select revision_id from instagram_post_state),
  'completed publication replay returns the same Revision'
);
select is(
  (select count(*) from public.asset_revisions
   where asset_id = (select asset_id from instagram_post_state)),
  1::bigint,
  'publication replay creates no duplicate Revision'
);

reset role;
set local role authenticated;
set local request.jwt.claim.sub = '81000000-0000-4000-8000-000000000001';
update instagram_post_state
set source_note_job_id = (
  public.enqueue_instagram_post_source_note_generation(asset_id) ->> 'id'
)::uuid;
select is(
  (select status from public.jobs
   where id = (select source_note_job_id from instagram_post_state)),
  'queued',
  'ready Instagram Post can enter the existing Source Note pipeline'
);

reset role;
set local role service_role;
select is(
  (select status from public.claim_job('source-note-worker', 'llm', 120)),
  'running',
  'Source Note Worker can claim the Instagram Post note Job'
);
select is(
  public.get_instagram_post_source_note_context(
    (select source_note_job_id from instagram_post_state),
    'source-note-worker', 1
  ) ->> 'sourceType',
  'instagram_post',
  'Source Note context identifies the Instagram Post source'
);
select is(
  jsonb_array_length(
    public.get_instagram_post_source_note_context(
      (select source_note_job_id from instagram_post_state),
      'source-note-worker', 1
    ) -> 'evidence'
  ),
  3,
  'Source Note context contains caption and image OCR Evidence'
);

select is(
  public.enqueue_instagram_post_retrieval_index_jobs(
    'test-embedding-model', 'test-v1', 25
  ),
  1,
  'ready Instagram Post creates one isolated retrieval indexing Job'
);
update instagram_post_state
set retrieval_job_id = (
  select id from public.jobs
  where asset_id = instagram_post_state.asset_id
    and job_type = 'index_retrieval'
  order by created_at desc limit 1
);
select is(
  (select status from public.claim_job('instagram-retrieval-worker', 'retrieval', 120)),
  'running',
  'retrieval Worker claims the Instagram Post indexing Job'
);
select is(
  public.get_instagram_post_retrieval_index_context(
    (select retrieval_job_id from instagram_post_state),
    'instagram-retrieval-worker', 1
  ) ->> 'sourceType',
  'instagram_post',
  'retrieval context identifies the Instagram Post source'
);

reset role;
select * from finish();
rollback;
