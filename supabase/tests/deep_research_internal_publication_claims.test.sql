begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(5);

select has_function(
  'public', 'publish_internal_deep_research_draft',
  array['uuid','text','bigint','text','text','text','text','text','text','jsonb'],
  'Deep internal publication wrapper exists'
);
select ok(has_function_privilege(
  'service_role',
  'public.publish_internal_deep_research_draft(uuid,text,bigint,text,text,text,text,text,text,jsonb)',
  'execute'
), 'service role may publish verified Deep internal claims');
select ok(not has_function_privilege(
  'authenticated',
  'public.publish_internal_deep_research_draft(uuid,text,bigint,text,text,text,text,text,text,jsonb)',
  'execute'
), 'authenticated clients cannot invoke Worker publication');
select ok((select relrowsecurity and relforcerowsecurity from pg_class
  where oid='public.claims'::regclass), 'published Claims retain forced RLS');
select ok((select relrowsecurity and relforcerowsecurity from pg_class
  where oid='public.research_citations'::regclass),
  'published citation provenance retains forced RLS');

select * from finish();
rollback;
