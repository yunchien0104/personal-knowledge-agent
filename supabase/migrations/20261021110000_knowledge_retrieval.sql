-- Step 5B: tenant-scoped PostgreSQL full-text retrieval across generated
-- Source Notes/Claims and original Evidence. Origin labels remain explicit.

create index notes_fts_idx on public.notes using gin (
  to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(body, ''))
);
create index claims_fts_idx on public.claims using gin (
  to_tsvector('simple', coalesce(text, ''))
);
create index asset_evidence_fts_idx on public.asset_evidence using gin (
  to_tsvector('simple', coalesce(excerpt, ''))
);

create or replace function public.search_knowledge(
  p_query text,
  p_limit integer default 20,
  p_offset integer default 0
)
returns table (
  result_type text,
  content_origin text,
  result_id uuid,
  title text,
  snippet text,
  relevance real,
  asset_id uuid,
  revision_id uuid,
  note_id uuid,
  claim_id uuid,
  citations jsonb,
  created_at timestamptz,
  total_count bigint
)
language plpgsql
stable
security invoker
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_query text := btrim(p_query);
  v_tsquery tsquery;
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  if v_query is null or char_length(v_query) not between 2 and 200
     or p_limit is null or p_limit not between 1 and 50
     or p_offset is null or p_offset not between 0 and 1000 then
    raise invalid_parameter_value using message = 'retrieval parameters are invalid';
  end if;
  v_tsquery := websearch_to_tsquery('simple', v_query);
  if numnode(v_tsquery) = 0 then
    return;
  end if;

  return query
  with note_matches as (
    select
      'source_note'::text as result_type,
      'generated_note'::text as content_origin,
      n.id as result_id,
      n.title,
      left(n.body, 1000) as snippet,
      ts_rank_cd(
        setweight(to_tsvector('simple', coalesce(n.title, '')), 'A')
          || setweight(to_tsvector('simple', coalesce(n.body, '')), 'B'),
        v_tsquery
      )::real as relevance,
      g.asset_id_snapshot as asset_id,
      g.source_revision_id_snapshot as revision_id,
      n.id as note_id,
      null::uuid as claim_id,
      coalesce(citation_set.citations, '[]'::jsonb) as citations,
      n.updated_at as created_at
    from public.notes n
    join public.source_note_generations g
      on g.note_id = n.id and g.user_id = n.user_id
    left join lateral (
      select jsonb_agg(item.citation order by item.created_at) as citations
      from (
        select jsonb_build_object(
          'assetId', g.asset_id_snapshot,
          'revisionId', ce.source_revision_id_snapshot,
          'evidenceId', ce.evidence_id_snapshot,
          'locator', ce.locator_json,
          'validityStatus', case
            when ce.evidence_id is null then 'source_deleted'
            else ce.validity_status
          end
        ) as citation, ce.created_at
        from public.claims c
        join public.claim_evidence ce
          on ce.claim_id = c.id and ce.user_id = c.user_id
        where c.note_id = n.id and c.user_id = v_user_id
        order by ce.created_at
        limit 20
      ) item
    ) citation_set on true
    where n.user_id = v_user_id and n.note_type = 'source_note'
      and to_tsvector('simple', coalesce(n.title, '') || ' ' || coalesce(n.body, ''))
          @@ v_tsquery
  ),
  claim_matches as (
    select
      'claim'::text as result_type,
      'generated_claim'::text as content_origin,
      c.id as result_id,
      n.title,
      left(c.text, 1000) as snippet,
      ts_rank_cd(to_tsvector('simple', coalesce(c.text, '')), v_tsquery)::real
        as relevance,
      g.asset_id_snapshot as asset_id,
      g.source_revision_id_snapshot as revision_id,
      c.note_id,
      c.id as claim_id,
      coalesce(citation_set.citations, '[]'::jsonb) as citations,
      c.created_at
    from public.claims c
    join public.notes n on n.id = c.note_id and n.user_id = c.user_id
    join public.source_note_generations g
      on g.note_id = c.note_id and g.user_id = c.user_id
    left join lateral (
      select jsonb_agg(jsonb_build_object(
        'assetId', g.asset_id_snapshot,
        'revisionId', ce.source_revision_id_snapshot,
        'evidenceId', ce.evidence_id_snapshot,
        'locator', ce.locator_json,
        'validityStatus', case
          when ce.evidence_id is null then 'source_deleted'
          else ce.validity_status
        end
      ) order by ce.created_at) as citations
      from (
        select * from public.claim_evidence
        where claim_id = c.id and user_id = v_user_id
        order by created_at
        limit 20
      ) ce
    ) citation_set on true
    where c.user_id = v_user_id
      and to_tsvector('simple', coalesce(c.text, '')) @@ v_tsquery
  ),
  evidence_matches as (
    select
      'evidence'::text as result_type,
      'original_source'::text as content_origin,
      e.id as result_id,
      'PDF source page ' || coalesce(e.locator_json ->> 'page_number', '?') as title,
      left(e.excerpt, 1000) as snippet,
      ts_rank_cd(to_tsvector('simple', coalesce(e.excerpt, '')), v_tsquery)::real
        as relevance,
      r.asset_id,
      e.revision_id,
      null::uuid as note_id,
      null::uuid as claim_id,
      jsonb_build_array(jsonb_build_object(
        'assetId', r.asset_id,
        'revisionId', e.revision_id,
        'evidenceId', e.id,
        'locator', e.locator_json,
        'validityStatus', 'valid'
      )) as citations,
      e.created_at
    from public.asset_evidence e
    join public.asset_revisions r
      on r.id = e.revision_id and r.user_id = e.user_id
    where e.user_id = v_user_id
      and to_tsvector('simple', coalesce(e.excerpt, '')) @@ v_tsquery
  ),
  matches as (
    select * from note_matches
    union all select * from claim_matches
    union all select * from evidence_matches
  )
  select
    m.result_type, m.content_origin, m.result_id, m.title, m.snippet,
    m.relevance, m.asset_id, m.revision_id, m.note_id, m.claim_id,
    m.citations, m.created_at, count(*) over() as total_count
  from matches m
  order by m.relevance desc, m.created_at desc, m.result_id
  limit p_limit offset p_offset;
end;
$$;

revoke all on function public.search_knowledge(text,integer,integer) from public;
grant execute on function public.search_knowledge(text,integer,integer) to authenticated;
