-- R1 Unified Hybrid Retrieval. This migration is additive: the deployed
-- get_research_context RPC and original Evidence rows are intentionally unchanged.

create extension if not exists vector with schema extensions;

create table public.retrieval_chunks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  asset_id uuid not null,
  revision_id uuid not null,
  evidence_id uuid not null,
  chunk_index integer not null check (chunk_index >= 0),
  content text not null check (char_length(content) between 1 and 4000),
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  start_offset integer not null check (start_offset >= 0),
  end_offset integer not null check (end_offset > start_offset),
  locator_json jsonb not null check (jsonb_typeof(locator_json) = 'object'),
  embedding extensions.vector(1536),
  embedding_model text not null check (char_length(embedding_model) between 1 and 128),
  embedding_version text not null check (embedding_version ~ '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$'),
  embedding_status text not null check (embedding_status in ('ready', 'failed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint retrieval_chunks_asset_owner_fk
    foreign key (asset_id, user_id)
    references public.assets (id, user_id) on delete cascade,
  constraint retrieval_chunks_revision_owner_fk
    foreign key (revision_id, user_id)
    references public.asset_revisions (id, user_id) on delete cascade,
  constraint retrieval_chunks_evidence_fk
    foreign key (evidence_id)
    references public.asset_evidence (id) on delete cascade,
  constraint retrieval_chunks_revision_position_model_unique
    unique (revision_id, chunk_index, embedding_model, embedding_version),
  constraint retrieval_chunks_embedding_state_check check (
    (embedding_status = 'ready' and embedding is not null)
    or (embedding_status = 'failed' and embedding is null)
  )
);

-- Keep the existing Evidence schema untouched while still enforcing the complete
-- Asset -> Revision -> Evidence tenant relationship on every Retrieval row.
create function public.validate_retrieval_chunk_provenance()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  perform 1
  from public.asset_revisions r
  join public.asset_evidence e
    on e.revision_id = r.id and e.user_id = r.user_id
  where r.id = new.revision_id and r.user_id = new.user_id
    and r.asset_id = new.asset_id and e.id = new.evidence_id;
  if not found then
    raise integrity_constraint_violation
      using message = 'retrieval chunk provenance is inconsistent';
  end if;
  return new;
end;
$$;
revoke all on function public.validate_retrieval_chunk_provenance() from public;
create trigger retrieval_chunks_validate_provenance
before insert or update
on public.retrieval_chunks
for each row execute function public.validate_retrieval_chunk_provenance();

create index retrieval_chunks_user_asset_revision_idx
  on public.retrieval_chunks (user_id, asset_id, revision_id);
create index retrieval_chunks_fts_idx
  on public.retrieval_chunks using gin (to_tsvector('simple', content));
-- R1 deliberately uses exact pgvector search. Add an ANN index only after
-- production corpus/latency measurements justify its recall trade-off.

create trigger retrieval_chunks_set_updated_at
before update on public.retrieval_chunks
for each row execute function public.set_current_timestamp_updated_at();

alter table public.retrieval_chunks enable row level security;
alter table public.retrieval_chunks force row level security;
create policy retrieval_chunks_select_own on public.retrieval_chunks
for select to authenticated using ((select auth.uid()) = user_id);

revoke all on public.retrieval_chunks from anon, authenticated;
grant select on public.retrieval_chunks to authenticated;
grant select, insert, update, delete on public.retrieval_chunks to service_role;

-- Incremental backfill only exposes active, validated, ready source revisions
-- whose exact model/version has no complete ready chunk set.
create or replace function public.get_retrieval_backfill_contexts(
  p_embedding_model text,
  p_embedding_version text,
  p_after_revision_id uuid default null,
  p_limit integer default 10
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_result jsonb;
begin
  if (select auth.role()) <> 'service_role' then
    raise insufficient_privilege using message = 'service role required';
  end if;
  if p_embedding_model is null or char_length(p_embedding_model) not between 1 and 128
     or p_embedding_version is null
     or p_embedding_version !~ '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$'
     or p_limit not between 1 and 50 then
    raise invalid_parameter_value using message = 'backfill request is invalid';
  end if;

  with candidates as (
    select a.id as asset_id, a.user_id, r.id as revision_id,
           r.fingerprint, r.normalized_object_key
    from public.assets a
    join public.asset_revisions r
      on r.id = a.active_revision_id and r.user_id = a.user_id
    -- R1's implemented artifact reader is PDF-specific. The retrieval table/RPC
    -- remain source-agnostic so a Threads chunker can publish into the same layer.
    where a.source_type = 'pdf' and a.status = 'ready' and r.status = 'validated'
      and r.normalized_object_key is not null
      and (p_after_revision_id is null or r.id > p_after_revision_id)
      and exists (
        select 1 from public.asset_evidence e
        where e.revision_id = r.id and e.user_id = r.user_id
      )
      and not exists (
        select 1 from public.retrieval_chunks c
        where c.revision_id = r.id and c.user_id = r.user_id
          and c.embedding_model = p_embedding_model
          and c.embedding_version = p_embedding_version
          and c.embedding_status = 'ready'
      )
    order by r.id
    limit p_limit
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'userId', c.user_id,
    'assetId', c.asset_id,
    'revisionId', c.revision_id,
    'sourceFingerprint', c.fingerprint,
    'artifactObjectKey', c.normalized_object_key,
    'evidence', (
      select jsonb_agg(jsonb_build_object(
        'id', e.id, 'sourceHash', e.source_hash,
        'locator', e.locator_json, 'excerpt', e.excerpt
      ) order by (e.locator_json ->> 'page_number')::integer, e.id)
      from public.asset_evidence e
      where e.revision_id = c.revision_id and e.user_id = c.user_id
    )
  ) order by c.revision_id), '[]'::jsonb)
  into v_result from candidates c;
  return v_result;
end;
$$;

-- Atomic and idempotent publication. All input is validated before stale chunks
-- are removed; a failed embedding run therefore cannot damage source ingestion.
create or replace function public.replace_retrieval_chunks(
  p_user_id uuid,
  p_asset_id uuid,
  p_revision_id uuid,
  p_embedding_model text,
  p_embedding_version text,
  p_chunks jsonb
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_chunk jsonb;
  v_embedding extensions.vector(1536);
  v_count integer;
begin
  if (select auth.role()) <> 'service_role' then
    raise insufficient_privilege using message = 'service role required';
  end if;
  if p_user_id is null or p_asset_id is null or p_revision_id is null
     or p_embedding_model is null or char_length(p_embedding_model) not between 1 and 128
     or p_embedding_version is null
     or p_embedding_version !~ '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$'
     or jsonb_typeof(p_chunks) is distinct from 'array'
     or jsonb_array_length(p_chunks) not between 1 and 5000 then
    raise invalid_parameter_value using message = 'retrieval publication is invalid';
  end if;
  perform 1 from public.assets a
  join public.asset_revisions r
    on r.id = a.active_revision_id and r.user_id = a.user_id and r.asset_id = a.id
  where a.id = p_asset_id and a.user_id = p_user_id and a.status = 'ready'
    and r.id = p_revision_id and r.status = 'validated';
  if not found then
    raise integrity_constraint_violation using message = 'active source revision is invalid';
  end if;

  for v_chunk in select value from jsonb_array_elements(p_chunks) loop
    if jsonb_typeof(v_chunk) is distinct from 'object'
       or (v_chunk ->> 'chunkIndex') !~ '^[0-9]+$'
       or v_chunk ->> 'evidenceId' is null
       or v_chunk ->> 'content' is null
       or char_length(v_chunk ->> 'content') not between 1 and 4000
       or (v_chunk ->> 'contentHash') !~ '^[0-9a-f]{64}$'
       or (v_chunk ->> 'startOffset') !~ '^[0-9]+$'
       or (v_chunk ->> 'endOffset') !~ '^[0-9]+$'
       or (v_chunk ->> 'endOffset')::integer <= (v_chunk ->> 'startOffset')::integer
       or jsonb_typeof(v_chunk -> 'locator') is distinct from 'object'
       or jsonb_typeof(v_chunk -> 'embedding') is distinct from 'array'
       or jsonb_array_length(v_chunk -> 'embedding') <> 1536 then
      raise invalid_parameter_value using message = 'retrieval chunk is invalid';
    end if;
    perform 1 from public.asset_evidence e
    where e.id = (v_chunk ->> 'evidenceId')::uuid
      and e.user_id = p_user_id and e.revision_id = p_revision_id
      and e.source_hash = (select fingerprint from public.asset_revisions
                           where id = p_revision_id and user_id = p_user_id);
    if not found then
      raise integrity_constraint_violation using message = 'retrieval Evidence mapping is invalid';
    end if;
    begin
      v_embedding := (v_chunk -> 'embedding')::text::extensions.vector(1536);
      if extensions.vector_norm(v_embedding) <= 0 then
        raise invalid_parameter_value using message = 'embedding vector is invalid';
      end if;
    exception when invalid_text_representation or data_exception then
      raise invalid_parameter_value using message = 'embedding vector is invalid';
    end;
  end loop;

  delete from public.retrieval_chunks c
  where c.revision_id = p_revision_id and c.user_id = p_user_id
    and c.embedding_model = p_embedding_model
    and c.embedding_version = p_embedding_version
    and not exists (
      select 1 from jsonb_array_elements(p_chunks) x
      where (x ->> 'chunkIndex')::integer = c.chunk_index
    );

  insert into public.retrieval_chunks (
    user_id, asset_id, revision_id, evidence_id, chunk_index, content,
    content_hash, start_offset, end_offset, locator_json, embedding,
    embedding_model, embedding_version, embedding_status
  )
  select p_user_id, p_asset_id, p_revision_id,
         (x ->> 'evidenceId')::uuid, (x ->> 'chunkIndex')::integer,
         x ->> 'content', x ->> 'contentHash',
         (x ->> 'startOffset')::integer, (x ->> 'endOffset')::integer,
         x -> 'locator', (x -> 'embedding')::text::extensions.vector(1536),
         p_embedding_model, p_embedding_version, 'ready'
  from jsonb_array_elements(p_chunks) x
  on conflict (revision_id, chunk_index, embedding_model, embedding_version)
  do update set
    evidence_id = excluded.evidence_id,
    content = excluded.content,
    content_hash = excluded.content_hash,
    start_offset = excluded.start_offset,
    end_offset = excluded.end_offset,
    locator_json = excluded.locator_json,
    embedding = excluded.embedding,
    embedding_status = 'ready',
    updated_at = now()
  where public.retrieval_chunks.user_id = excluded.user_id
    and public.retrieval_chunks.asset_id = excluded.asset_id;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

-- Private core used by the authenticated and worker wrappers below. It combines
-- bounded OR-style lexical candidates and exact semantic candidates with RRF.
create or replace function public.hybrid_retrieve_for_user(
  p_user_id uuid,
  p_query text,
  p_query_embedding extensions.vector(1536),
  p_embedding_model text,
  p_embedding_version text,
  p_scope_asset_ids uuid[],
  p_top_k integer,
  p_candidate_limit integer
)
returns jsonb
language sql
stable
security definer
set search_path = ''
set statement_timeout = '5s'
as $$
  with eligible as materialized (
    select c.*
    from public.retrieval_chunks c
    join public.asset_revisions r
      on r.id = c.revision_id and r.user_id = c.user_id
      and r.asset_id = c.asset_id and r.status = 'validated'
    join public.assets a
      on a.id = c.asset_id and a.user_id = c.user_id
      and a.active_revision_id = r.id and a.status = 'ready'
    where c.user_id = p_user_id and c.embedding_status = 'ready'
      and c.embedding_model = p_embedding_model
      and c.embedding_version = p_embedding_version
      and (p_scope_asset_ids is null or c.asset_id = any(p_scope_asset_ids))
  ),
  query_lexemes as (
    select lexeme
    from unnest(tsvector_to_array(to_tsvector('simple', left(p_query, 500)))) lexeme
    where char_length(lexeme) between 1 and 80
    order by lexeme
    limit 24
  ),
  lexical_scores as (
    select e.id,
           sum(ts_rank_cd(to_tsvector('simple', e.content), plainto_tsquery('simple', q.lexeme))) as score
    from eligible e
    join query_lexemes q
      on to_tsvector('simple', e.content) @@ plainto_tsquery('simple', q.lexeme)
    group by e.id
  ),
  lexical as (
    select id, row_number() over (order by score desc, id) as lexical_rank
    from lexical_scores order by score desc, id limit p_candidate_limit
  ),
  semantic as (
    select e.id,
           row_number() over (order by e.embedding OPERATOR(extensions.<=>) p_query_embedding, e.id) as semantic_rank,
           1 - (e.embedding OPERATOR(extensions.<=>) p_query_embedding) as similarity
    from eligible e
    where p_query_embedding is not null
    order by e.embedding OPERATOR(extensions.<=>) p_query_embedding, e.id
    limit p_candidate_limit
  ),
  fused as (
    select coalesce(l.id, s.id) id, l.lexical_rank, s.semantic_rank, s.similarity,
           coalesce(1.0 / (60 + l.lexical_rank), 0)
             + coalesce(1.0 / (60 + s.semantic_rank), 0) as rrf_score
    from lexical l full join semantic s on s.id = l.id
  ),
  selected as (
    select e.*, f.lexical_rank, f.semantic_rank, f.similarity, f.rrf_score,
           case when f.lexical_rank is not null and f.semantic_rank is not null then 'hybrid'
                when f.lexical_rank is not null then 'lexical' else 'semantic' end method
    from fused f join eligible e on e.id = f.id
    order by f.rrf_score desc, f.similarity desc nulls last, e.id
    limit p_top_k
  )
  select jsonb_build_object(
    'semanticStatus', case when p_query_embedding is null
      then 'degraded_embedding_unavailable' else 'ready' end,
    'sufficiency', 'unassessed',
    'scopeAssetIds', coalesce(to_jsonb(p_scope_asset_ids), 'null'::jsonb),
    'results', coalesce(jsonb_agg(jsonb_build_object(
      'chunkId', s.id, 'assetId', s.asset_id, 'revisionId', s.revision_id,
      'evidenceId', s.evidence_id, 'content', s.content,
      'contentHash', s.content_hash, 'locator', s.locator_json,
      'startOffset', s.start_offset, 'endOffset', s.end_offset,
      'retrievalMethod', s.method, 'rrfScore', s.rrf_score,
      'lexicalRank', s.lexical_rank, 'semanticRank', s.semantic_rank,
      'semanticSimilarity', s.similarity
    ) order by s.rrf_score desc, s.similarity desc nulls last, s.id), '[]'::jsonb)
  ) from selected s;
$$;

create or replace function public.hybrid_retrieve(
  p_query text,
  p_query_embedding extensions.vector(1536),
  p_embedding_model text,
  p_embedding_version text,
  p_scope_asset_ids uuid[] default null,
  p_top_k integer default 12,
  p_candidate_limit integer default 40
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
set statement_timeout = '5s'
as $$
declare
  v_user_id uuid := auth.uid();
begin
  if v_user_id is null then raise insufficient_privilege using message = 'authentication required'; end if;
  if p_query is null or char_length(btrim(p_query)) not between 2 and 500
     or p_embedding_model is null or char_length(p_embedding_model) not between 1 and 128
     or p_embedding_version is null
     or p_embedding_version !~ '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$'
     or p_top_k not between 1 and 50 or p_candidate_limit not between p_top_k and 200
     or (p_scope_asset_ids is not null and cardinality(p_scope_asset_ids) not between 1 and 100) then
    raise invalid_parameter_value using message = 'hybrid retrieval request is invalid';
  end if;
  if p_scope_asset_ids is not null and exists (
    select 1 from unnest(p_scope_asset_ids) requested(id)
    where not exists (
      select 1 from public.assets a
      where a.id = requested.id and a.user_id = v_user_id and a.status = 'ready'
    )
  ) then
    raise insufficient_privilege using message = 'retrieval scope contains unavailable asset';
  end if;
  return public.hybrid_retrieve_for_user(
    v_user_id, p_query, p_query_embedding, p_embedding_model,
    p_embedding_version, p_scope_asset_ids, p_top_k, p_candidate_limit
  );
end;
$$;

create or replace function public.hybrid_retrieve_for_worker(
  p_user_id uuid,
  p_query text,
  p_query_embedding extensions.vector(1536),
  p_embedding_model text,
  p_embedding_version text,
  p_scope_asset_ids uuid[] default null,
  p_top_k integer default 12,
  p_candidate_limit integer default 40
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
set statement_timeout = '5s'
as $$
begin
  if (select auth.role()) <> 'service_role' then
    raise insufficient_privilege using message = 'service role required';
  end if;
  if p_user_id is null or p_query is null or char_length(btrim(p_query)) not between 2 and 500
     or p_embedding_model is null or char_length(p_embedding_model) not between 1 and 128
     or p_embedding_version is null
     or p_embedding_version !~ '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$'
     or p_top_k not between 1 and 50 or p_candidate_limit not between p_top_k and 200
     or (p_scope_asset_ids is not null and cardinality(p_scope_asset_ids) not between 1 and 100) then
    raise invalid_parameter_value using message = 'hybrid retrieval request is invalid';
  end if;
  if p_scope_asset_ids is not null and exists (
    select 1 from unnest(p_scope_asset_ids) requested(id)
    where not exists (
      select 1 from public.assets a
      where a.id = requested.id and a.user_id = p_user_id and a.status = 'ready'
    )
  ) then
    raise insufficient_privilege using message = 'retrieval scope contains unavailable asset';
  end if;
  return public.hybrid_retrieve_for_user(
    p_user_id, p_query, p_query_embedding, p_embedding_model,
    p_embedding_version, p_scope_asset_ids, p_top_k, p_candidate_limit
  );
end;
$$;

revoke all on function public.get_retrieval_backfill_contexts(text,text,uuid,integer)
  from public, anon, authenticated, service_role;
revoke all on function public.replace_retrieval_chunks(uuid,uuid,uuid,text,text,jsonb)
  from public, anon, authenticated, service_role;
revoke all on function public.hybrid_retrieve_for_user(uuid,text,extensions.vector,text,text,uuid[],integer,integer)
  from public, anon, authenticated, service_role;
revoke all on function public.hybrid_retrieve(text,extensions.vector,text,text,uuid[],integer,integer)
  from public, anon, authenticated, service_role;
revoke all on function public.hybrid_retrieve_for_worker(uuid,text,extensions.vector,text,text,uuid[],integer,integer)
  from public, anon, authenticated, service_role;
grant execute on function public.get_retrieval_backfill_contexts(text,text,uuid,integer)
  to service_role;
grant execute on function public.replace_retrieval_chunks(uuid,uuid,uuid,text,text,jsonb)
  to service_role;
grant execute on function public.hybrid_retrieve(text,extensions.vector,text,text,uuid[],integer,integer)
  to authenticated;
grant execute on function public.hybrid_retrieve_for_worker(uuid,text,extensions.vector,text,text,uuid[],integer,integer)
  to service_role;
