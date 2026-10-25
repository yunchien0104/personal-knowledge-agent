-- Production Research Pipeline integration. Additive data changes only; deployed
-- migrations remain immutable. Apply to isolated Staging before any production use.

-- Fail closed instead of waiting indefinitely behind application traffic. These
-- bounds must be validated in Staging with a current-size data clone.
set lock_timeout = '5s';
set statement_timeout = '15min';

create extension if not exists vector with schema extensions;

create or replace function public.research_search_text(p_text text)
returns text
language plpgsql
immutable
strict
set search_path = ''
as $$
declare
  v_text text := regexp_replace(
    replace(replace(p_text, E'\r\n', E'\n'), E'\r', E'\n'),
    E'[ \t\f\v]+', ' ', 'g'
  );
  v_output text := '';
  v_terms text := '';
  v_character text;
  v_previous text;
  v_next text;
  v_length integer := char_length(v_text);
begin
  for v_index in 1..v_length loop
    v_character := substr(v_text, v_index, 1);
    if v_character = E'\n' then
      v_previous := case when v_index > 1 then substr(v_text, v_index - 1, 1) else '' end;
      v_next := case when v_index < v_length then substr(v_text, v_index + 1, 1) else '' end;
      if v_previous ~ '[㐀-鿿豈-﫿ぁ-ヿ가-힯]'
         and v_next ~ '[㐀-鿿豈-﫿ぁ-ヿ가-힯]' then
        continue;
      end if;
      v_character := ' ';
    end if;
    v_output := v_output || v_character;
    if v_character ~ '[㐀-鿿豈-﫿ぁ-ヿ가-힯]' then
      v_terms := v_terms || ' ' || v_character;
      if v_index < v_length
         and substr(v_text, v_index + 1, 1) ~ '[㐀-鿿豈-﫿ぁ-ヿ가-힯]' then
        v_terms := v_terms || ' ' || v_character || substr(v_text, v_index + 1, 1);
      end if;
    end if;
  end loop;
  return btrim(regexp_replace(lower(v_output || v_terms), '\s+', ' ', 'g'));
end;
$$;

alter table public.jobs add constraint jobs_resource_class_check_v2 check (
  resource_class in (
    'lightweight', 'media', 'llm', 'research', 'opportunity', 'deletion', 'retrieval'
  )
) not valid;
alter table public.jobs validate constraint jobs_resource_class_check_v2;
alter table public.jobs drop constraint jobs_resource_class_check;
alter table public.jobs rename constraint jobs_resource_class_check_v2
  to jobs_resource_class_check;

create or replace function public.claim_job(
  p_worker_id text,
  p_resource_class text default null,
  p_lease_seconds integer default 120
)
returns setof public.jobs
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
begin
  if p_worker_id is null or char_length(p_worker_id) not between 1 and 128 then
    raise invalid_parameter_value using message = 'worker id is invalid';
  end if;
  if p_resource_class is not null and p_resource_class not in (
    'lightweight', 'media', 'llm', 'research', 'opportunity', 'deletion', 'retrieval'
  ) then
    raise invalid_parameter_value using message = 'resource class is invalid';
  end if;
  if p_lease_seconds not between 30 and 600 then
    raise invalid_parameter_value using message = 'lease duration is invalid';
  end if;
  loop
    select * into v_job from public.jobs
    where cancel_requested_at is null
      and (p_resource_class is null or resource_class = p_resource_class)
      and (
        (status in ('queued', 'retry_wait') and next_run_at <= now())
        or (status = 'running' and lease_expires_at <= now())
      )
    order by (
      priority + least(100, greatest(0,
        floor(extract(epoch from (now() - created_at)) / 3600)::integer
      ))
    ) desc, created_at, id
    limit 1 for update skip locked;
    if not found then return; end if;
    if v_job.status = 'retry_wait' then
      update public.jobs set status = 'queued' where id = v_job.id;
      v_job.status := 'queued';
    end if;
    if v_job.attempt_count >= v_job.max_attempts then
      update public.jobs set
        status = 'failed', lease_owner = null, lease_expires_at = null,
        last_error_class = 'permanent', last_error_code = 'attempts_exhausted',
        last_error_message = 'Maximum processing attempts were exhausted.'
      where id = v_job.id;
      update public.job_attempts set
        ended_at = coalesce(ended_at, now()), outcome = coalesce(outcome, 'lease_expired'),
        error_class = coalesce(error_class, 'transient'),
        error_code = coalesce(error_code, 'lease_expired')
      where job_id = v_job.id and ended_at is null;
      if v_job.job_type = 'ingest_pdf' then
        update public.assets set status = 'failed' where id = v_job.asset_id;
      end if;
      continue;
    end if;
    if v_job.status = 'running' then
      update public.job_attempts set
        ended_at = now(), outcome = 'lease_expired', error_class = 'transient',
        error_code = 'lease_expired',
        error_message = 'Worker lease expired before completion.'
      where job_id = v_job.id and lease_generation = v_job.lease_generation
        and ended_at is null;
    end if;
    update public.jobs set
      status = 'running', attempt_count = attempt_count + 1,
      lease_owner = p_worker_id,
      lease_expires_at = now() + make_interval(secs => p_lease_seconds),
      lease_generation = lease_generation + 1
    where id = v_job.id returning * into v_job;
    insert into public.job_attempts (
      user_id, job_id, attempt_no, lease_generation, worker_id
    ) values (
      v_job.user_id, v_job.id, v_job.attempt_count, v_job.lease_generation, p_worker_id
    );
    if v_job.job_type = 'ingest_pdf' then
      update public.assets set status = 'processing' where id = v_job.asset_id;
    end if;
    return next v_job;
    return;
  end loop;
end;
$$;

create or replace function public.enqueue_retrieval_index_jobs(
  p_embedding_model text,
  p_embedding_version text,
  p_limit integer default 25
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_count integer;
begin
  if p_embedding_model is null or char_length(p_embedding_model) not between 1 and 128
     or p_embedding_version is null
     or p_embedding_version !~ '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$'
     or p_limit not between 1 and 100 then
    raise invalid_parameter_value using message = 'retrieval enqueue request is invalid';
  end if;
  with candidates as (
    select a.user_id, a.id asset_id, r.id revision_id
    from public.assets a
    join public.asset_revisions r
      on r.id = a.active_revision_id and r.user_id = a.user_id
      and r.asset_id = a.id and r.status = 'validated'
    where a.source_type = 'pdf' and a.status = 'ready'
      and r.normalized_object_key is not null
      and not exists (
        select 1 from public.retrieval_chunks c
        where c.user_id = a.user_id and c.revision_id = r.id
          and c.embedding_model = p_embedding_model
          and c.embedding_version = p_embedding_version
          and c.embedding_status = 'ready'
      )
    order by r.created_at, r.id
    limit p_limit
  ), inserted as (
    insert into public.jobs (
      id, user_id, asset_id, job_type, payload_json, payload_schema_version,
      idempotency_key, resource_class, priority, max_attempts
    )
    select job.id, c.user_id, c.asset_id, 'index_retrieval',
      jsonb_build_object(
        'schemaVersion', 1, 'jobId', job.id, 'userId', c.user_id,
        'jobType', 'index_retrieval', 'idempotencyKey', job.key,
        'input', jsonb_build_object(
          'assetId', c.asset_id, 'revisionId', c.revision_id,
          'embeddingModel', p_embedding_model, 'embeddingVersion', p_embedding_version
        )
      ), 1, job.key, 'retrieval', 90, 5
    from candidates c
    cross join lateral (
      select gen_random_uuid() id,
        'retrieval-index:' || c.revision_id::text || ':'
          || md5(p_embedding_model || ':' || p_embedding_version) key
    ) job
    on conflict (user_id, idempotency_key) do nothing
    returning 1
  )
  select count(*) into v_count from inserted;
  return v_count;
end;
$$;

create or replace function public.get_retrieval_index_context(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_revision public.asset_revisions%rowtype;
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'index_retrieval'
     or v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then return null; end if;
  select r.* into v_revision
  from public.asset_revisions r
  join public.assets a
    on a.id = r.asset_id and a.user_id = r.user_id
    and a.active_revision_id = r.id and a.status = 'ready'
  where r.id = (v_job.payload_json #>> '{input,revisionId}')::uuid
    and r.user_id = v_job.user_id and r.asset_id = v_job.asset_id
    and r.status = 'validated';
  if not found then
    raise integrity_constraint_violation using message = 'retrieval source revision is no longer active';
  end if;
  return jsonb_build_object(
    'userId', v_job.user_id, 'assetId', v_job.asset_id,
    'revisionId', v_revision.id, 'sourceFingerprint', v_revision.fingerprint,
    'artifactObjectKey', v_revision.normalized_object_key,
    'evidence', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'id', e.id, 'sourceHash', e.source_hash,
        'locator', e.locator_json, 'excerpt', e.excerpt
      ) order by (e.locator_json ->> 'page_number')::integer, e.id), '[]'::jsonb)
      from public.asset_evidence e
      where e.revision_id = v_revision.id and e.user_id = v_job.user_id
    )
  );
end;
$$;

create or replace function public.publish_retrieval_index(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_embedding_model text,
  p_embedding_version text,
  p_chunks jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_count integer;
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'index_retrieval' then return null; end if;
  if v_job.status = 'completed' then
    return jsonb_build_object('jobId', v_job.id, 'idempotent', true);
  end if;
  if v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now()
     or v_job.payload_json #>> '{input,embeddingModel}' <> p_embedding_model
     or v_job.payload_json #>> '{input,embeddingVersion}' <> p_embedding_version then
    return null;
  end if;
  v_count := public.replace_retrieval_chunks(
    v_job.user_id, v_job.asset_id,
    (v_job.payload_json #>> '{input,revisionId}')::uuid,
    p_embedding_model, p_embedding_version, p_chunks
  );
  update public.job_attempts set ended_at = now(), heartbeat_at = now(), outcome = 'completed'
  where job_id = v_job.id and lease_generation = p_lease_generation
    and worker_id = p_worker_id and ended_at is null;
  update public.jobs set
    status = 'completed', lease_owner = null, lease_expires_at = null,
    checkpoint_ref = v_job.payload_json #>> '{input,revisionId}',
    last_error_class = null, last_error_code = null, last_error_message = null
  where id = v_job.id;
  return jsonb_build_object('jobId', v_job.id, 'chunkCount', v_count, 'idempotent', false);
end;
$$;

revoke all on function public.claim_job(text,text,integer)
  from public, anon, authenticated, service_role;
revoke all on function public.enqueue_retrieval_index_jobs(text,text,integer)
  from public, anon, authenticated, service_role;
revoke all on function public.get_retrieval_index_context(uuid,text,bigint)
  from public, anon, authenticated, service_role;
revoke all on function public.publish_retrieval_index(uuid,text,bigint,text,text,jsonb)
  from public, anon, authenticated, service_role;
grant execute on function public.claim_job(text,text,integer) to service_role;
grant execute on function public.enqueue_retrieval_index_jobs(text,text,integer) to service_role;
grant execute on function public.get_retrieval_index_context(uuid,text,bigint) to service_role;
grant execute on function public.publish_retrieval_index(uuid,text,bigint,text,text,jsonb)
  to service_role;

-- Persist the authenticated scope selected at request time. The array is never
-- accepted as a user identity and is revalidated against owned active Assets.
alter table public.research_runs
  add column scope_asset_ids uuid[],
  add column retrieval_status text,
  add column progress_json jsonb not null default '{}'::jsonb
    check (jsonb_typeof(progress_json) = 'object'),
  add constraint research_runs_scope_check check (
    scope_asset_ids is null or cardinality(scope_asset_ids) between 1 and 100
  ),
  add constraint research_runs_retrieval_status_check check (
    retrieval_status is null or retrieval_status in (
      'ready', 'degraded_embedding_unavailable', 'insufficient'
    )
  );

create table public.research_run_evidence (
  user_id uuid not null,
  research_run_id uuid not null,
  evidence_id uuid not null,
  asset_id uuid not null,
  revision_id uuid not null,
  retrieval_rank integer not null check (retrieval_rank between 1 and 50),
  retrieval_score double precision not null check (retrieval_score >= 0),
  retrieval_method text not null check (retrieval_method in ('lexical','semantic','hybrid')),
  query_hits integer not null check (query_hits between 1 and 3),
  created_at timestamptz not null default now(),
  primary key (research_run_id, evidence_id),
  constraint research_run_evidence_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint research_run_evidence_asset_owner_fk
    foreign key (asset_id, user_id)
    references public.assets (id, user_id) on delete cascade,
  constraint research_run_evidence_revision_owner_fk
    foreign key (revision_id, user_id)
    references public.asset_revisions (id, user_id) on delete cascade,
  constraint research_run_evidence_evidence_fk
    foreign key (evidence_id)
    references public.asset_evidence (id) on delete cascade
);

create index research_run_evidence_user_run_rank_idx
  on public.research_run_evidence (user_id, research_run_id, retrieval_rank);
create index research_run_evidence_evidence_idx
  on public.research_run_evidence (evidence_id);
create index research_run_evidence_asset_idx
  on public.research_run_evidence (asset_id);
create index research_run_evidence_revision_idx
  on public.research_run_evidence (revision_id);

alter table public.research_run_evidence enable row level security;
alter table public.research_run_evidence force row level security;
create policy research_run_evidence_select_own on public.research_run_evidence
for select to authenticated using ((select auth.uid()) = user_id);
revoke all on public.research_run_evidence from anon, authenticated;
grant select on public.research_run_evidence to authenticated;
grant select, insert, update, delete on public.research_run_evidence to service_role;

create or replace function public.start_research(
  p_question text,
  p_request_key uuid,
  p_scope_asset_ids uuid[]
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_question text := btrim(p_question);
  v_run public.research_runs%rowtype;
  v_job_id uuid := gen_random_uuid();
  v_key text := 'research:' || p_request_key::text;
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  if v_question is null or char_length(v_question) not between 3 and 500
     or p_request_key is null
     or (p_scope_asset_ids is not null and cardinality(p_scope_asset_ids) not between 1 and 100)
     or (p_scope_asset_ids is not null and (
       select count(*) <> count(distinct requested.id)
       from unnest(p_scope_asset_ids) requested(id)
     )) then
    raise invalid_parameter_value using message = 'research request is invalid';
  end if;
  if p_scope_asset_ids is not null and exists (
    select 1 from unnest(p_scope_asset_ids) requested(id)
    where not exists (
      select 1 from public.assets a
      join public.asset_revisions r
        on r.id = a.active_revision_id and r.user_id = a.user_id
        and r.asset_id = a.id and r.status = 'validated'
      where a.id = requested.id and a.user_id = v_user_id and a.status = 'ready'
    )
  ) then
    raise insufficient_privilege using message = 'research scope contains unavailable asset';
  end if;

  insert into public.research_runs (user_id, request_key, question, scope_asset_ids)
  values (v_user_id, p_request_key, v_question, p_scope_asset_ids)
  on conflict (user_id, request_key) do nothing;

  select * into strict v_run from public.research_runs
  where user_id = v_user_id and request_key = p_request_key
  for update;
  if v_run.question <> v_question
     or v_run.scope_asset_ids is distinct from p_scope_asset_ids then
    raise integrity_constraint_violation using message = 'request key conflicts with another research request';
  end if;

  if v_run.job_id is null then
    insert into public.jobs (
      id, user_id, asset_id, job_type, payload_json, payload_schema_version,
      idempotency_key, resource_class, priority
    ) values (
      v_job_id, v_user_id, null, 'execute_research',
      jsonb_build_object(
        'schemaVersion', 1,
        'jobId', v_job_id,
        'userId', v_user_id,
        'jobType', 'execute_research',
        'idempotencyKey', v_key,
        'input', jsonb_strip_nulls(jsonb_build_object(
          'researchRunId', v_run.id,
          'question', v_run.question,
          'scopeAssetIds', p_scope_asset_ids
        ))
      ),
      1, v_key, 'research', 120
    );
    update public.research_runs set job_id = v_job_id
    where id = v_run.id returning * into v_run;
  end if;

  return jsonb_build_object(
    'id', v_run.id, 'jobId', v_run.job_id, 'status', v_run.status
  );
end;
$$;

create or replace function public.start_research(p_question text, p_request_key uuid)
returns jsonb
language sql
security definer
set search_path = ''
as $$
  select public.start_research(p_question, p_request_key, null::uuid[]);
$$;

create or replace function public.get_research_context(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_plan jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'execute_research'
     or v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then
    return null;
  end if;
  select * into v_run from public.research_runs
  where id = (v_job.payload_json #>> '{input,researchRunId}')::uuid
    and user_id = v_job.user_id and job_id = v_job.id
  for update;
  if not found or v_run.question <> (v_job.payload_json #>> '{input,question}')
     or v_run.scope_asset_ids is distinct from
        array(select jsonb_array_elements_text(v_job.payload_json #> '{input,scopeAssetIds}')::uuid)
        and (v_job.payload_json #> '{input,scopeAssetIds}') is not null then
    return null;
  end if;
  if jsonb_typeof(p_plan) is distinct from 'object'
     or p_plan ->> 'question' <> v_run.question
     or jsonb_typeof(p_plan -> 'subquestions') is distinct from 'array'
     or jsonb_array_length(p_plan -> 'subquestions') not between 1 and 5
     or jsonb_typeof(p_plan -> 'lexicalQueries') is distinct from 'array'
     or jsonb_array_length(p_plan -> 'lexicalQueries') not between 1 and 5
     or jsonb_typeof(p_plan -> 'semanticQueries') is distinct from 'array'
     or jsonb_array_length(p_plan -> 'semanticQueries') not between 1 and 5
     or jsonb_typeof(p_plan -> 'searchKeywords') is distinct from 'array'
     or jsonb_array_length(p_plan -> 'searchKeywords') not between 1 and 24
     or (p_plan ->> 'maxQueries')::integer not between 1 and 3
     or (p_plan ->> 'maxRounds')::integer <> 1
     or (p_plan ->> 'maxCostUsd')::numeric <> 0 then
    raise invalid_parameter_value using message = 'research plan is invalid';
  end if;
  update public.research_runs set
    plan_json = p_plan,
    status = 'running',
    progress_json = jsonb_build_object('stage', 'retrieving', 'updatedAt', now())
  where id = v_run.id;
  return jsonb_build_object(
    'researchRunId', v_run.id,
    'question', v_run.question,
    'scopeAssetIds', coalesce(to_jsonb(v_run.scope_asset_ids), 'null'::jsonb)
  );
end;
$$;

create or replace function public.validate_research_run_evidence()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  perform 1
  from public.research_runs rr
  join public.assets a on a.id = new.asset_id and a.user_id = new.user_id
  join public.asset_revisions r
    on r.id = new.revision_id and r.user_id = new.user_id and r.asset_id = a.id
  join public.asset_evidence e
    on e.id = new.evidence_id and e.user_id = new.user_id and e.revision_id = r.id
  where rr.id = new.research_run_id and rr.user_id = new.user_id
    and a.active_revision_id = r.id and a.status = 'ready' and r.status = 'validated'
    and (rr.scope_asset_ids is null or a.id = any(rr.scope_asset_ids));
  if not found then
    raise integrity_constraint_violation using message = 'research retrieval provenance is invalid';
  end if;
  return new;
end;
$$;

revoke all on function public.validate_research_run_evidence() from public;
create trigger research_run_evidence_validate_provenance
before insert or update on public.research_run_evidence
for each row execute function public.validate_research_run_evidence();

create or replace function public.record_research_retrieval(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_plan jsonb,
  p_retrieval_status text,
  p_evidence jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_row jsonb;
  v_rank integer := 0;
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'execute_research'
     or v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then return null; end if;
  select * into v_run from public.research_runs
  where id = (v_job.payload_json #>> '{input,researchRunId}')::uuid
    and user_id = v_job.user_id and job_id = v_job.id for update;
  if not found or v_run.plan_json is distinct from p_plan then return null; end if;
  if p_retrieval_status not in ('ready','degraded_embedding_unavailable')
     or jsonb_typeof(p_evidence) is distinct from 'array'
     or jsonb_array_length(p_evidence) not between 1 and 12 then
    raise invalid_parameter_value using message = 'research retrieval checkpoint is invalid';
  end if;

  delete from public.research_run_evidence where research_run_id = v_run.id;
  for v_row in select value from jsonb_array_elements(p_evidence) loop
    v_rank := v_rank + 1;
    if (v_row ->> 'id') is null or (v_row ->> 'assetId') is null
       or (v_row ->> 'revisionId') is null
       or (v_row ->> 'retrievalMethod') not in ('lexical','semantic','hybrid')
       or (v_row ->> 'retrievalScore')::double precision < 0
       or (v_row ->> 'queryHits')::integer not between 1 and 3 then
      raise invalid_parameter_value using message = 'research retrieval Evidence is invalid';
    end if;
    insert into public.research_run_evidence (
      user_id, research_run_id, evidence_id, asset_id, revision_id,
      retrieval_rank, retrieval_score, retrieval_method, query_hits
    ) values (
      v_job.user_id, v_run.id, (v_row ->> 'id')::uuid,
      (v_row ->> 'assetId')::uuid, (v_row ->> 'revisionId')::uuid,
      v_rank, (v_row ->> 'retrievalScore')::double precision,
      v_row ->> 'retrievalMethod', (v_row ->> 'queryHits')::integer
    );
  end loop;
  update public.research_runs set
    retrieval_status = p_retrieval_status,
    progress_json = jsonb_build_object(
      'stage', 'generating', 'retrievedEvidence', v_rank,
      'degraded', p_retrieval_status <> 'ready', 'updatedAt', now()
    )
  where id = v_run.id;
  return jsonb_build_object('researchRunId', v_run.id, 'evidenceCount', v_rank);
end;
$$;

create or replace function public.require_research_citation_retrieval()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  perform 1 from public.research_run_evidence rre
  where rre.research_run_id = new.research_run_id
    and rre.user_id = new.user_id
    and rre.evidence_id = new.evidence_id
    and rre.asset_id = new.asset_id
    and rre.revision_id = new.source_revision_id;
  if not found then
    raise integrity_constraint_violation
      using message = 'research citation was not selected by authenticated retrieval';
  end if;
  return new;
end;
$$;

revoke all on function public.require_research_citation_retrieval() from public;
create trigger research_citations_require_retrieval
before insert on public.research_citations
for each row execute function public.require_research_citation_retrieval();

revoke all on function public.start_research(text,uuid,uuid[])
  from public, anon, authenticated, service_role;
revoke all on function public.start_research(text,uuid)
  from public, anon, authenticated, service_role;
revoke all on function public.get_research_context(uuid,text,bigint,jsonb)
  from public, anon, authenticated, service_role;
revoke all on function public.record_research_retrieval(uuid,text,bigint,jsonb,text,jsonb)
  from public, anon, authenticated, service_role;
grant execute on function public.start_research(text,uuid,uuid[]) to authenticated;
grant execute on function public.start_research(text,uuid) to authenticated;
grant execute on function public.get_research_context(uuid,text,bigint,jsonb) to service_role;
grant execute on function public.record_research_retrieval(uuid,text,bigint,jsonb,text,jsonb)
  to service_role;

revoke all on function public.research_search_text(text)
  from public, anon, authenticated, service_role;

alter table public.retrieval_chunks
  add column search_text text;

update public.retrieval_chunks
set search_text = public.research_search_text(content)
where search_text is null;

alter table public.retrieval_chunks
  alter column search_text set not null,
  add constraint retrieval_chunks_search_text_check
    check (char_length(search_text) between 1 and 12000),
  add column search_vector tsvector
    generated always as (to_tsvector('simple'::regconfig, search_text)) stored;

drop index if exists public.retrieval_chunks_fts_idx;
create index retrieval_chunks_search_vector_idx
  on public.retrieval_chunks using gin (search_vector);
create index retrieval_chunks_active_lookup_idx
  on public.retrieval_chunks
  (user_id, embedding_model, embedding_version, asset_id, revision_id);
create index retrieval_chunks_embedding_hnsw_idx
  on public.retrieval_chunks
  using hnsw (embedding extensions.vector_cosine_ops)
  where embedding_status = 'ready' and embedding is not null;

comment on column public.retrieval_chunks.content is
  'Exact citation-preserving text copied from immutable Evidence.';
comment on column public.retrieval_chunks.search_text is
  'Unicode/CJK-repaired retrieval representation; never used as quoted Evidence.';

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
  with query_lexemes as materialized (
    select lexeme
    from unnest(tsvector_to_array(to_tsvector(
      'simple'::regconfig,
      public.research_search_text(left(p_query, 500))
    ))) lexeme
    where char_length(lexeme) between 1 and 80
    order by lexeme
    limit 24
  ),
  lexical_scores as materialized (
    select c.id,
           sum(ts_rank_cd(c.search_vector, plainto_tsquery('simple'::regconfig, q.lexeme))) score
    from query_lexemes q
    join public.retrieval_chunks c
      on c.search_vector @@ plainto_tsquery('simple'::regconfig, q.lexeme)
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
    group by c.id
    order by score desc, c.id
    limit p_candidate_limit
  ),
  lexical as (
    select id, row_number() over (order by score desc, id) lexical_rank
    from lexical_scores
  ),
  semantic_candidates as materialized (
    select c.id,
           c.embedding OPERATOR(extensions.<=>) p_query_embedding distance
    from public.retrieval_chunks c
    join public.asset_revisions r
      on r.id = c.revision_id and r.user_id = c.user_id
      and r.asset_id = c.asset_id and r.status = 'validated'
    join public.assets a
      on a.id = c.asset_id and a.user_id = c.user_id
      and a.active_revision_id = r.id and a.status = 'ready'
    where p_query_embedding is not null
      and c.user_id = p_user_id and c.embedding_status = 'ready'
      and c.embedding is not null
      and c.embedding_model = p_embedding_model
      and c.embedding_version = p_embedding_version
      and (p_scope_asset_ids is null or c.asset_id = any(p_scope_asset_ids))
    order by c.embedding OPERATOR(extensions.<=>) p_query_embedding, c.id
    limit p_candidate_limit
  ),
  semantic as (
    select id, row_number() over (order by distance, id) semantic_rank,
           1 - distance similarity
    from semantic_candidates
  ),
  fused as (
    select coalesce(l.id, s.id) id, l.lexical_rank, s.semantic_rank, s.similarity,
           coalesce(1.0 / (60 + l.lexical_rank), 0)
             + coalesce(1.0 / (60 + s.semantic_rank), 0) rrf_score
    from lexical l full join semantic s on s.id = l.id
  ),
  selected as (
    select c.*, f.lexical_rank, f.semantic_rank, f.similarity, f.rrf_score,
           case when f.lexical_rank is not null and f.semantic_rank is not null then 'hybrid'
                when f.lexical_rank is not null then 'lexical' else 'semantic' end method
    from fused f
    join public.retrieval_chunks c on c.id = f.id
    where coalesce(f.similarity, 1) >= 0.20
    order by f.rrf_score desc, f.similarity desc nulls last, c.id
    limit p_top_k
  )
  select jsonb_build_object(
    'semanticStatus', case when p_query_embedding is null
      then 'degraded_embedding_unavailable' else 'ready' end,
    'sufficiency', case when count(*) = 0 then 'insufficient' else 'unassessed' end,
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
       or v_chunk ->> 'searchText' is null
       or char_length(v_chunk ->> 'searchText') not between 1 and 12000
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
    search_text, content_hash, start_offset, end_offset, locator_json, embedding,
    embedding_model, embedding_version, embedding_status
  )
  select p_user_id, p_asset_id, p_revision_id,
         (x ->> 'evidenceId')::uuid, (x ->> 'chunkIndex')::integer,
         x ->> 'content', x ->> 'searchText', x ->> 'contentHash',
         (x ->> 'startOffset')::integer, (x ->> 'endOffset')::integer,
         x -> 'locator', (x -> 'embedding')::text::extensions.vector(1536),
         p_embedding_model, p_embedding_version, 'ready'
  from jsonb_array_elements(p_chunks) x
  on conflict (revision_id, chunk_index, embedding_model, embedding_version)
  do update set
    evidence_id = excluded.evidence_id,
    content = excluded.content,
    search_text = excluded.search_text,
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

-- These SECURITY DEFINER helpers accept an explicit user id, so they must never
-- be callable from PostgREST roles. The fenced public entry points above are the
-- only supported path; service_role retains publication access for the worker.
revoke all on function public.hybrid_retrieve_for_user(
  uuid,text,extensions.vector,text,text,uuid[],integer,integer
) from public, anon, authenticated, service_role;
revoke all on function public.replace_retrieval_chunks(
  uuid,uuid,uuid,text,text,jsonb
) from public, anon, authenticated, service_role;
grant execute on function public.replace_retrieval_chunks(
  uuid,uuid,uuid,text,text,jsonb
) to service_role;

create or replace function public.hybrid_retrieve_for_research_job(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
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
  v_job public.jobs%rowtype;
  v_scope uuid[];
begin
  select * into v_job from public.jobs
  where id = p_job_id and job_type = 'execute_research'
    and status = 'running' and lease_owner = p_worker_id
    and lease_generation = p_lease_generation and lease_expires_at > now();
  if not found then
    raise insufficient_privilege using message = 'active research lease required';
  end if;
  if p_query is null or char_length(btrim(p_query)) not between 2 and 500
     or p_embedding_model is null or char_length(p_embedding_model) not between 1 and 128
     or p_embedding_version is null
     or p_embedding_version !~ '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$'
     or p_top_k not between 1 and 50
     or p_candidate_limit not between p_top_k and 200 then
    raise invalid_parameter_value using message = 'hybrid retrieval request is invalid';
  end if;
  v_scope := case
    when v_job.payload_json #> '{input,scopeAssetIds}' is null then null
    else array(
      select jsonb_array_elements_text(v_job.payload_json #> '{input,scopeAssetIds}')::uuid
    )
  end;
  if v_scope is distinct from p_scope_asset_ids then
    raise insufficient_privilege using message = 'research scope does not match trusted Job scope';
  end if;
  if v_scope is not null and exists (
    select 1 from unnest(v_scope) requested(id)
    where not exists (
      select 1 from public.assets a
      join public.asset_revisions r
        on r.id = a.active_revision_id and r.user_id = a.user_id
        and r.asset_id = a.id and r.status = 'validated'
      where a.id = requested.id and a.user_id = v_job.user_id and a.status = 'ready'
    )
  ) then
    raise insufficient_privilege using message = 'research scope contains unavailable asset';
  end if;
  return public.hybrid_retrieve_for_user(
    v_job.user_id, p_query, p_query_embedding, p_embedding_model,
    p_embedding_version, v_scope, p_top_k, p_candidate_limit
  );
end;
$$;

revoke all on function public.hybrid_retrieve_for_research_job(
  uuid,text,bigint,text,extensions.vector,text,text,uuid[],integer,integer
) from public, anon, authenticated, service_role;
grant execute on function public.hybrid_retrieve_for_research_job(
  uuid,text,bigint,text,extensions.vector,text,text,uuid[],integer,integer
) to service_role;

-- The unbound Worker RPC from R1 is superseded by the fenced Job-bound RPC.
revoke execute on function public.hybrid_retrieve_for_worker(
  uuid,text,extensions.vector,text,text,uuid[],integer,integer
) from service_role;
