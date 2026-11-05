-- Reference Sidebar sources are deliberately classified in the database. This
-- keeps Research Reports, Source Notes and Personal Notes from sharing an
-- untyped notes query, while preserving RLS and immutable source provenance.

create index if not exists notes_user_type_created_idx
  on public.notes (user_id, note_type, created_at desc, id desc);

alter table public.personal_note_imports
  drop constraint if exists personal_note_imports_source_kind_check;
alter table public.personal_note_imports
  add constraint personal_note_imports_source_kind_check check (
    source_kind in (
      'knowledge','personal','threads','reels','research','docx','markdown','spreadsheet'
    )
  );

create or replace function public.list_reference_sources(
  p_category text,
  p_source_type text default null,
  p_search text default null,
  p_current_note_id uuid default null,
  p_limit integer default 20,
  p_offset integer default 0
)
returns table (
  id uuid,
  category text,
  title text,
  summary text,
  content text,
  source_type text,
  source_url text,
  source_asset_id uuid,
  source_revision_id uuid,
  research_run_id uuid,
  created_at timestamptz,
  total_count bigint
)
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if auth.uid() is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  if p_category is null or p_category not in ('research', 'knowledge', 'personal') then
    raise invalid_parameter_value using message = 'invalid reference category';
  end if;
  if p_source_type is not null
     and (p_category <> 'knowledge'
       or p_source_type not in ('pdf','instagram_reel','instagram_post','threads')) then
    raise invalid_parameter_value using message = 'invalid knowledge source type';
  end if;
  if p_search is not null and char_length(p_search) > 200 then
    raise invalid_parameter_value using message = 'invalid search';
  end if;
  if p_limit is null or p_limit not between 1 and 50
     or p_offset is null or p_offset < 0 then
    raise invalid_parameter_value using message = 'invalid pagination';
  end if;

  return query
  with knowledge_ranked as (
    select
      n.id,
      n.title,
      n.body,
      n.created_at,
      a.source_type,
      a.source_url,
      g.asset_id_snapshot,
      g.source_revision_id_snapshot,
      row_number() over (
        partition by g.asset_id_snapshot
        order by n.created_at desc, n.id desc
      ) as source_version_rank
    from public.notes n
    join public.source_note_generations g
      on g.note_id = n.id and g.user_id = n.user_id
    left join public.assets a
      on a.id = g.asset_id_snapshot and a.user_id = g.user_id
    left join public.asset_revisions ar
      on ar.id = g.source_revision_id_snapshot
      and ar.asset_id = g.asset_id_snapshot
      and ar.user_id = g.user_id
    where p_category = 'knowledge'
      and n.user_id = (select auth.uid())
      and n.note_type = 'source_note'
      and n.publication_state <> 'archived'
      and (
        p_source_type is null
        or (ar.id is not null and a.source_type = p_source_type)
      )
      and (
        p_search is null or btrim(p_search) = ''
        or n.title ilike '%' || btrim(p_search) || '%'
        or n.body ilike '%' || btrim(p_search) || '%'
        or a.source_url ilike '%' || btrim(p_search) || '%'
      )
  ), candidates as (
    select
      rr.output_note_id as id,
      'research'::text as category,
      n.title,
      left(btrim(regexp_replace(n.body, E'[#*_`>\\[\\]()]', '', 'g')), 420) as summary,
      n.body as content,
      null::text as source_type,
      null::text as source_url,
      null::uuid as source_asset_id,
      null::uuid as source_revision_id,
      rr.id as research_run_id,
      n.created_at
    from public.research_runs rr
    join public.notes n
      on n.id = rr.output_note_id and n.user_id = rr.user_id
    where p_category = 'research'
      and rr.user_id = (select auth.uid())
      and rr.status in ('draft_ready', 'completed')
      and n.note_type = 'research_note'
      and n.publication_state = 'published'
      and (
        p_search is null or btrim(p_search) = ''
        or n.title ilike '%' || btrim(p_search) || '%'
        or n.body ilike '%' || btrim(p_search) || '%'
      )

    union all

    select
      k.id,
      'knowledge'::text,
      k.title,
      left(btrim(regexp_replace(k.body, E'[#*_`>\\[\\]()]', '', 'g')), 420),
      k.body,
      k.source_type,
      k.source_url,
      k.asset_id_snapshot,
      k.source_revision_id_snapshot,
      null::uuid,
      k.created_at
    from knowledge_ranked k
    where k.source_version_rank = 1

    union all

    select
      n.id,
      'personal'::text,
      n.title,
      left(btrim(coalesce(d.plain_text, n.body)), 420),
      coalesce(d.plain_text, n.body),
      null::text,
      null::text,
      null::uuid,
      null::uuid,
      null::uuid,
      n.created_at
    from public.notes n
    left join public.personal_note_documents d
      on d.note_id = n.id and d.user_id = n.user_id
    where p_category = 'personal'
      and n.user_id = (select auth.uid())
      and n.note_type = 'manual_note'
      and n.id is distinct from p_current_note_id
      and n.publication_state <> 'archived'
      and (
        p_search is null or btrim(p_search) = ''
        or n.title ilike '%' || btrim(p_search) || '%'
        or coalesce(d.plain_text, n.body) ilike '%' || btrim(p_search) || '%'
      )
  ), counted as (
    select c.*, count(*) over () as total_count
    from candidates c
  )
  select c.id, c.category, c.title, c.summary, c.content,
    c.source_type, c.source_url, c.source_asset_id, c.source_revision_id,
    c.research_run_id, c.created_at, c.total_count
  from counted c
  order by c.created_at desc, c.id desc
  limit p_limit offset p_offset;
end;
$$;

revoke all on function public.list_reference_sources(text,text,text,uuid,integer,integer)
  from public, anon;
grant execute on function public.list_reference_sources(text,text,text,uuid,integer,integer)
  to authenticated;

comment on function public.list_reference_sources(text,text,text,uuid,integer,integer) is
  'RLS-aware, stably ordered Reference Sidebar search across strictly separated source categories.';
