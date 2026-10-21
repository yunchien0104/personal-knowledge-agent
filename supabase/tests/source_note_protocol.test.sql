begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(22);

select ok(not has_function_privilege('anon', 'public.enqueue_source_note_generation(uuid)', 'execute'), 'anon cannot enqueue');
select ok(has_function_privilege('authenticated', 'public.enqueue_source_note_generation(uuid)', 'execute'), 'authenticated can enqueue');
select ok(not has_function_privilege('anon', 'public.get_source_note_context(uuid,text,bigint)', 'execute'), 'anon cannot read Worker context');
select ok(not has_function_privilege('authenticated', 'public.publish_source_note(uuid,text,bigint,text,text,text,text,text,text,jsonb)', 'execute'), 'authenticated cannot publish');
select ok(has_function_privilege('service_role', 'public.publish_source_note(uuid,text,bigint,text,text,text,text,text,text,jsonb)', 'execute'), 'service role can publish');

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values (
  '00000000-0000-0000-0000-000000000000',
  '71000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated',
  'source-note@example.test', '', now(),
  '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now()
);
insert into public.assets (id, user_id, source_type) values (
  '72000000-0000-4000-8000-000000000002',
  '71000000-0000-4000-8000-000000000001', 'pdf'
);
insert into public.asset_revisions (
  id, user_id, asset_id, revision_no, fingerprint, processor_version, status, normalized_object_key
) values (
  '73000000-0000-4000-8000-000000000003',
  '71000000-0000-4000-8000-000000000001',
  '72000000-0000-4000-8000-000000000002', 1, repeat('a', 64),
  'native-text-pypdf-v1', 'validated',
  '71000000-0000-4000-8000-000000000001/72000000-0000-4000-8000-000000000002/revisions/a/native.json'
);
update public.assets set status = 'ready', active_revision_id = '73000000-0000-4000-8000-000000000003'
where id = '72000000-0000-4000-8000-000000000002';
insert into public.asset_artifacts (
  user_id, revision_id, kind, object_key, sha256, mime, size_bytes, retention_class
) values (
  '71000000-0000-4000-8000-000000000001', '73000000-0000-4000-8000-000000000003',
  'native_pdf_text',
  '71000000-0000-4000-8000-000000000001/72000000-0000-4000-8000-000000000002/revisions/a/native.json',
  repeat('b', 64), 'application/json', 100, 'source_derived'
);
insert into public.asset_evidence (
  id, user_id, revision_id, locator_type, locator_json, excerpt, source_hash, content_object_key
) values (
  '74000000-0000-4000-8000-000000000004',
  '71000000-0000-4000-8000-000000000001', '73000000-0000-4000-8000-000000000003',
  'pdf', '{"kind":"pdf_page","page_number":1,"start_offset":0,"end_offset":35}'::jsonb,
  'Revenue increased by 20 percent.', repeat('a', 64),
  '71000000-0000-4000-8000-000000000001/72000000-0000-4000-8000-000000000002/revisions/a/native.json'
);

create temporary table source_note_state (job_id uuid, note_id uuid);
grant select, update, insert on source_note_state to authenticated, service_role;
set local role authenticated;
set local request.jwt.claim.sub = '71000000-0000-4000-8000-000000000001';
insert into source_note_state (job_id)
select (public.enqueue_source_note_generation('72000000-0000-4000-8000-000000000002') ->> 'id')::uuid;
select is((select status from public.jobs where id = (select job_id from source_note_state)), 'queued', 'owned active Revision is queued');
select is(
  (public.enqueue_source_note_generation('72000000-0000-4000-8000-000000000002') ->> 'id')::uuid,
  (select job_id from source_note_state), 'enqueue is idempotent'
);

reset role;
set local role service_role;
select is((select status from public.claim_job('source-note-worker', 'llm', 120)), 'running', 'LLM Worker claims Job');
select is((select status from public.assets where id = '72000000-0000-4000-8000-000000000002'), 'ready', 'Source Note claim does not downgrade ready Asset');
select is(
  public.get_source_note_context((select job_id from source_note_state), 'source-note-worker', 1) ->> 'revisionId',
  '73000000-0000-4000-8000-000000000003', 'context is fenced to active Revision'
);
select is(
  jsonb_array_length(public.get_source_note_context((select job_id from source_note_state), 'source-note-worker', 1) -> 'evidence'),
  1, 'context contains Revision Evidence'
);

select throws_matching(
  $$select public.publish_source_note(
    (select job_id from source_note_state), 'source-note-worker', 1,
    'mock', 'mock-v1', 'source-note-v1', repeat('c', 64), 'Title', 'Body',
    '[{"text":"Unsupported","category":"fact","citations":[{"evidenceId":"74000000-0000-4000-8000-000000000004","quote":"not in source"}]}]'::jsonb
  )$$,
  'citation is not supported by source evidence', 'unsupported quote is rejected'
);
select is((select count(*) from public.notes where user_id = '71000000-0000-4000-8000-000000000001'), 0::bigint, 'invalid output publishes no Note');

update source_note_state set note_id = (
  public.publish_source_note(
    job_id, 'source-note-worker', 1, 'mock', 'mock-v1', 'source-note-v1',
    repeat('c', 64), 'Revenue Note', '## Summary\nRevenue increased.',
    '[
      {"text":"Revenue increased.","category":"fact","citations":[{"evidenceId":"74000000-0000-4000-8000-000000000004","quote":"Revenue increased"}]},
      {"text":"The increase was 20 percent.","category":"data","citations":[{"evidenceId":"74000000-0000-4000-8000-000000000004","quote":"20 percent"}]}
    ]'::jsonb
  ) ->> 'noteId'
)::uuid;
select is((select note_type from public.notes where id = (select note_id from source_note_state)), 'source_note', 'publication creates Source Note');
select is((select count(*) from public.claims where note_id = (select note_id from source_note_state)), 2::bigint, 'publication creates Claims');
select is((select count(*) from public.claim_evidence where claim_id in (select id from public.claims where note_id = (select note_id from source_note_state))), 2::bigint, 'publication creates claim citations');
select is((select min((locator_json ->> 'page_number')::integer) from public.claim_evidence where claim_id in (select id from public.claims where note_id = (select note_id from source_note_state))), 1, 'citation retains PDF page');
select ok((select bool_and(verification_status = 'unreviewed' and needs_review) from public.claims where note_id = (select note_id from source_note_state)), 'generated Claims are not falsely verified');
select is(
  (public.publish_source_note(
    (select job_id from source_note_state), 'source-note-worker', 1,
    'mock', 'mock-v1', 'source-note-v1', repeat('c', 64), 'Revenue Note', 'ignored', '[]'::jsonb
  ) ->> 'noteId')::uuid,
  (select note_id from source_note_state), 'completed publication replay returns same Note'
);
select is((select count(*) from public.source_note_generations where job_id = (select job_id from source_note_state)), 1::bigint, 'replay creates no duplicate generation');

reset role;
set local role authenticated;
set local request.jwt.claim.sub = '71000000-0000-4000-8000-000000000001';
select is(
  (public.update_note_content((select note_id from source_note_state), 1, 'Edited', 'User body') ->> 'body_revision')::integer,
  2, 'optimistic edit increments body revision'
);
select is((select count(*) from public.claim_evidence where claim_id in (select id from public.claims where note_id = (select note_id from source_note_state))), 2::bigint, 'editing Note does not overwrite Evidence');

reset role;
select * from finish();
rollback;
