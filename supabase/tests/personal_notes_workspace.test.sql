begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(16);

select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid='public.personal_note_documents'::regclass),'documents force RLS');
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid='public.personal_note_versions'::regclass),'versions force RLS');
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid='public.personal_note_imports'::regclass),'imports force RLS');
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid='public.personal_note_citations'::regclass),'citations force RLS');

insert into auth.users(instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,raw_app_meta_data,raw_user_meta_data,created_at,updated_at) values
('00000000-0000-0000-0000-000000000000','11111111-1111-4111-8111-111111111111','authenticated','authenticated','notes-a@example.test','',now(),'{}','{}',now(),now()),
('00000000-0000-0000-0000-000000000000','22222222-2222-4222-8222-222222222222','authenticated','authenticated','notes-b@example.test','',now(),'{}','{}',now(),now());
insert into public.notes(id,user_id,note_type,title,body,publication_state) values
('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','11111111-1111-4111-8111-111111111111','manual_note','User A note','Synthetic','draft'),
('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','22222222-2222-4222-8222-222222222222','manual_note','User B note','Synthetic','draft');

set local role authenticated;
set local request.jwt.claim.sub='11111111-1111-4111-8111-111111111111';
insert into public.personal_note_documents(note_id,user_id,content_json,plain_text) values
('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','11111111-1111-4111-8111-111111111111','{"type":"doc","content":[]}','Synthetic');
select is((select count(*) from public.personal_note_documents),1::bigint,'owner reads document');
select is((select count(*) from public.notes where id='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'),0::bigint,'owner cannot read other note');
select throws_matching(
  $$insert into public.personal_note_documents(note_id,user_id,content_json,plain_text) values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','22222222-2222-4222-8222-222222222222','{}','x')$$,
  'row-level security|personal note data requires an owned manual_note',
  'cross-account document insert denied'
);

insert into public.personal_note_versions(user_id,note_id,body_revision,title,content_json,plain_text,reason) values
('11111111-1111-4111-8111-111111111111','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',1,'User A note','{"type":"doc","content":[]}','Synthetic','manual');
select is((select count(*) from public.personal_note_versions),1::bigint,'owner reads version');
insert into public.personal_note_imports(id,user_id,note_id,source_kind,snapshot_title,snapshot_text) values
('cccccccc-cccc-4ccc-8ccc-cccccccccccc','11111111-1111-4111-8111-111111111111','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','knowledge','Synthetic knowledge','Imported text');
select is((select count(*) from public.personal_note_imports),1::bigint,'owner reads import');
insert into public.personal_note_citations(user_id,note_id,import_id,source_label,supporting_quote,locator_json) values
('11111111-1111-4111-8111-111111111111','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','cccccccc-cccc-4ccc-8ccc-cccccccccccc','Synthetic source','Synthetic quote','{"page":1}');
select is((select count(*) from public.personal_note_citations),1::bigint,'owner reads citation');

set local request.jwt.claim.sub='22222222-2222-4222-8222-222222222222';
select is((select count(*) from public.personal_note_documents),0::bigint,'other user cannot read document');
select is((select count(*) from public.personal_note_versions),0::bigint,'other user cannot read versions');
select is((select count(*) from public.personal_note_imports),0::bigint,'other user cannot read imports');
select is((select count(*) from public.personal_note_citations),0::bigint,'other user cannot read citations');

reset role;
select ok(not has_table_privilege('anon','public.personal_note_documents','select'),'anon cannot select documents');
select ok(not has_table_privilege('anon','public.personal_note_citations','select'),'anon cannot select citations');
select * from finish();
rollback;
