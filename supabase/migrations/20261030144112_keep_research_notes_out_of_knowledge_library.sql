-- Research notes remain in public.notes and are still protected by the existing
-- ownership RLS. They are presented only through the Research workspace, so the
-- general Knowledge projection must not return them.
create or replace function public.list_knowledge_notes(
  p_search text default null,
  p_topic_id uuid default null,
  p_unclassified boolean default false,
  p_limit integer default 24,
  p_offset integer default 0
)
returns table (
  id uuid, note_type text, title text, summary text, publication_state text,
  created_at timestamptz, updated_at timestamptz, topics jsonb,
  manual_topic_override boolean, source_asset_id uuid, source_status text,
  total_count bigint
)
language sql
security invoker
set search_path = ''
as $$
  with ranked as (
    select n.*, g.asset_id_snapshot,
      case when n.note_type = 'source_note' then
        row_number() over (partition by g.asset_id_snapshot order by g.created_at desc, n.updated_at desc, n.id desc)
      else 1 end as version_rank
    from public.notes n
    left join public.source_note_generations g on g.note_id = n.id and g.user_id = n.user_id
    where n.user_id = (select auth.uid())
      and n.publication_state <> 'archived'
      and n.note_type <> 'research_note'
  ), eligible as (
    select r.*, a.status as asset_status
    from ranked r left join public.assets a
      on a.id = r.asset_id_snapshot and a.user_id = r.user_id
    where r.version_rank = 1
      and (p_search is null or btrim(p_search) = '' or
        r.title ilike '%' || p_search || '%' or r.body ilike '%' || p_search || '%')
      and (p_topic_id is null or exists (
        select 1 from public.note_topics nt where nt.note_id = r.id and nt.user_id = r.user_id
          and nt.topic_id = p_topic_id
      ))
      and (not p_unclassified or not exists (
        select 1 from public.note_topics nt where nt.note_id = r.id and nt.user_id = r.user_id
      ))
  )
  select e.id, e.note_type, e.title,
    left(btrim(regexp_replace(e.body, E'[#*_`>\\[\\]()]', '', 'g')), 420) as summary,
    e.publication_state, e.created_at, e.updated_at,
    coalesce((select jsonb_agg(jsonb_build_object('id', t.id, 'name', t.name)
      order by t.name) from public.note_topics nt join public.topics t
      on t.id = nt.topic_id and t.user_id = nt.user_id
      where nt.note_id = e.id and nt.user_id = e.user_id and t.merged_into_id is null), '[]'::jsonb),
    coalesce((select s.manual_override from public.note_topic_states s where s.note_id = e.id), false),
    e.asset_id_snapshot, e.asset_status, count(*) over ()
  from eligible e order by e.updated_at desc, e.id desc
  limit greatest(1, least(p_limit, 100)) offset greatest(p_offset, 0);
$$;

revoke all on function public.list_knowledge_notes(text,uuid,boolean,integer,integer) from public, anon;
grant execute on function public.list_knowledge_notes(text,uuid,boolean,integer,integer) to authenticated;
