-- Step 6: bounded, internal-only Research Agent with immutable Evidence citations.
-- Research starts only through an authenticated user RPC. The worker retrieves
-- active original Evidence for that user and publishes an editable draft only.

alter table public.jobs alter column asset_id drop not null;
alter table public.jobs drop constraint jobs_resource_class_check;
alter table public.jobs add constraint jobs_resource_class_check
  check (resource_class in ('lightweight', 'media', 'llm', 'research'));
alter table public.jobs drop constraint jobs_payload_identity_check;
alter table public.jobs add constraint jobs_payload_identity_check check (
  payload_json ?& array[
    'schemaVersion', 'jobId', 'userId', 'jobType', 'idempotencyKey', 'input'
  ]
  and payload_json ->> 'schemaVersion' = payload_schema_version::text
  and jsonb_typeof(payload_json -> 'input') = 'object'
  and payload_json ->> 'jobId' = id::text
  and payload_json ->> 'userId' = user_id::text
  and payload_json ->> 'jobType' = job_type
  and payload_json ->> 'idempotencyKey' = idempotency_key
  and (
    (
      asset_id is not null
      and (payload_json -> 'input') ? 'assetId'
      and payload_json #>> '{input,assetId}' = asset_id::text
    )
    or (
      asset_id is null
      and job_type = 'execute_research'
      and not (payload_json -> 'input') ? 'assetId'
      and (payload_json -> 'input') ? 'researchRunId'
    )
  )
);

create table public.research_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  request_key uuid not null,
  question text not null check (char_length(question) between 3 and 500),
  plan_json jsonb check (plan_json is null or jsonb_typeof(plan_json) = 'object'),
  status text not null default 'queued' check (
    status in ('queued', 'running', 'retry_wait', 'draft_ready', 'failed', 'cancelled')
  ),
  job_id uuid unique references public.jobs (id) on delete set null,
  output_note_id uuid,
  model_provider text check (
    model_provider is null or char_length(model_provider) between 1 and 64
  ),
  model_name text check (
    model_name is null or char_length(model_name) between 1 and 128
  ),
  prompt_version text check (
    prompt_version is null or prompt_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'
  ),
  output_hash text check (output_hash is null or output_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint research_runs_user_request_unique unique (user_id, request_key),
  constraint research_runs_id_user_unique unique (id, user_id),
  constraint research_runs_output_note_owner_fk
    foreign key (output_note_id, user_id)
    references public.notes (id, user_id)
    on delete set null (output_note_id)
);

create index research_runs_user_created_idx
  on public.research_runs (user_id, created_at desc);
create index research_runs_user_status_idx
  on public.research_runs (user_id, status, updated_at desc);

create table public.research_citations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  research_run_id uuid not null,
  note_id uuid not null,
  finding_index integer not null check (finding_index between 0 and 99),
  citation_index integer not null check (citation_index between 0 and 19),
  relation_type text not null check (
    relation_type in ('supports', 'contradicts', 'contextualizes')
  ),
  evidence_id uuid references public.asset_evidence (id) on delete set null,
  evidence_id_snapshot uuid not null,
  asset_id uuid references public.assets (id) on delete set null,
  asset_id_snapshot uuid not null,
  source_revision_id uuid references public.asset_revisions (id) on delete set null,
  source_revision_id_snapshot uuid not null,
  locator_json jsonb not null check (jsonb_typeof(locator_json) = 'object'),
  supporting_quote text not null check (char_length(supporting_quote) between 1 and 4000),
  created_at timestamptz not null default now(),
  constraint research_citations_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id)
    on delete cascade,
  constraint research_citations_note_owner_fk
    foreign key (note_id, user_id)
    references public.notes (id, user_id)
    on delete cascade,
  constraint research_citations_position_unique
    unique (research_run_id, finding_index, citation_index)
);

create index research_citations_user_run_idx
  on public.research_citations (user_id, research_run_id, finding_index, citation_index);
create index research_citations_evidence_fk_idx on public.research_citations (evidence_id);
create index research_citations_asset_fk_idx on public.research_citations (asset_id);
create index research_citations_revision_fk_idx
  on public.research_citations (source_revision_id);

alter table public.research_runs enable row level security;
alter table public.research_runs force row level security;
alter table public.research_citations enable row level security;
alter table public.research_citations force row level security;

create policy research_runs_select_own on public.research_runs
for select to authenticated using ((select auth.uid()) = user_id);
create policy research_citations_select_own on public.research_citations
for select to authenticated using ((select auth.uid()) = user_id);

revoke all on public.research_runs, public.research_citations from anon, authenticated;
grant select on public.research_runs, public.research_citations to authenticated;
grant select, insert, update, delete on public.research_runs, public.research_citations
  to service_role;

create trigger research_runs_set_updated_at
before update on public.research_runs
for each row execute function public.set_current_timestamp_updated_at();

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
  if p_resource_class is not null
     and p_resource_class not in ('lightweight', 'media', 'llm', 'research') then
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
      priority + least(
        100,
        greatest(0, floor(extract(epoch from (now() - created_at)) / 3600)::integer)
      )
    ) desc, created_at, id
    limit 1
    for update skip locked;

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
        ended_at = coalesce(ended_at, now()),
        outcome = coalesce(outcome, 'lease_expired'),
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

revoke execute on function public.claim_job(text,text,integer)
  from public, anon, authenticated, service_role;
grant execute on function public.claim_job(text,text,integer) to service_role;

create or replace function public.start_research(
  p_question text,
  p_request_key uuid
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
     or p_request_key is null then
    raise invalid_parameter_value using message = 'research request is invalid';
  end if;

  insert into public.research_runs (user_id, request_key, question)
  values (v_user_id, p_request_key, v_question)
  on conflict (user_id, request_key) do nothing;

  select * into strict v_run from public.research_runs
  where user_id = v_user_id and request_key = p_request_key
  for update;
  if v_run.question <> v_question then
    raise integrity_constraint_violation using message = 'request key conflicts with another question';
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
        'input', jsonb_build_object('researchRunId', v_run.id, 'question', v_run.question)
      ),
      1, v_key, 'research', 120
    );
    update public.research_runs set job_id = v_job_id
    where id = v_run.id returning * into v_run;
  end if;

  return jsonb_build_object(
    'id', v_run.id,
    'jobId', v_run.job_id,
    'status', v_run.status
  );
end;
$$;

create or replace function public.sync_research_run_job_status()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if new.job_type = 'execute_research' and new.status is distinct from old.status then
    update public.research_runs
    set status = case
      when new.status = 'running' then 'running'
      when new.status = 'retry_wait' then 'retry_wait'
      when new.status = 'failed' then 'failed'
      when new.status = 'cancelled' then 'cancelled'
      when new.status = 'queued' then 'queued'
      when new.status = 'completed' and output_note_id is not null then 'draft_ready'
      when new.status = 'completed' then 'failed'
      else status
    end
    where job_id = new.id;
  end if;
  return new;
end;
$$;

revoke all on function public.sync_research_run_job_status() from public;
create trigger jobs_sync_research_run_status
after update of status on public.jobs
for each row execute function public.sync_research_run_job_status();

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
  v_max_queries integer;
  v_evidence jsonb;
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
  if not found then return null; end if;

  if jsonb_typeof(p_plan) is distinct from 'object'
     or p_plan ->> 'question' <> v_run.question
     or jsonb_typeof(p_plan -> 'subquestions') is distinct from 'array'
     or jsonb_array_length(p_plan -> 'subquestions') not between 1 and 5
     or jsonb_typeof(p_plan -> 'evidenceRequirements') is distinct from 'array'
     or jsonb_array_length(p_plan -> 'evidenceRequirements') not between 1 and 10
     or jsonb_typeof(p_plan -> 'stopConditions') is distinct from 'array'
     or jsonb_array_length(p_plan -> 'stopConditions') not between 1 and 10
     or jsonb_typeof(p_plan -> 'maxCostUsd') is distinct from 'number'
     or (p_plan ->> 'maxCostUsd')::numeric <> 0
     or jsonb_typeof(p_plan -> 'maxQueries') is distinct from 'number'
     or (p_plan ->> 'maxQueries')::integer not between 1 and 3
     or jsonb_typeof(p_plan -> 'maxRounds') is distinct from 'number'
     or (p_plan ->> 'maxRounds')::integer <> 1 then
    raise invalid_parameter_value using message = 'research plan is invalid';
  end if;
  v_max_queries := (p_plan ->> 'maxQueries')::integer;

  with query_terms as (
    select value #>> '{}' as query_text, ordinality
    from jsonb_array_elements(p_plan -> 'subquestions') with ordinality
    where jsonb_typeof(value) = 'string'
      and char_length(btrim(value #>> '{}')) between 2 and 200
    order by ordinality
    limit v_max_queries
  ),
  ranked as (
    select
      e.id,
      r.asset_id,
      e.revision_id,
      e.excerpt,
      e.locator_json,
      max(ts_rank_cd(to_tsvector('simple', coalesce(e.excerpt, '')), q.tsq)) as relevance
    from query_terms qt
    cross join lateral websearch_to_tsquery('simple', qt.query_text) q(tsq)
    join public.asset_evidence e
      on e.user_id = v_job.user_id
      and to_tsvector('simple', coalesce(e.excerpt, '')) @@ q.tsq
    join public.asset_revisions r
      on r.id = e.revision_id and r.user_id = e.user_id and r.status = 'validated'
    join public.assets a
      on a.id = r.asset_id and a.user_id = r.user_id
      and a.active_revision_id = r.id and a.status = 'ready'
    where numnode(q.tsq) > 0
    group by e.id, r.asset_id, e.revision_id, e.excerpt, e.locator_json
    order by relevance desc, e.id
    limit 20
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', id,
    'assetId', asset_id,
    'revisionId', revision_id,
    'excerpt', excerpt,
    'locator', locator_json,
    'relevance', relevance
  ) order by relevance desc, id), '[]'::jsonb)
  into v_evidence
  from ranked;

  update public.research_runs set plan_json = p_plan, status = 'running'
  where id = v_run.id;
  return jsonb_build_object(
    'researchRunId', v_run.id,
    'question', v_run.question,
    'plan', p_plan,
    'evidence', v_evidence
  );
end;
$$;

create or replace function public.publish_research_draft(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_model_provider text,
  p_model_name text,
  p_prompt_version text,
  p_output_hash text,
  p_title text,
  p_body text,
  p_findings jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_note_id uuid;
  v_finding jsonb;
  v_citation jsonb;
  v_evidence public.asset_evidence%rowtype;
  v_asset_id uuid;
  v_finding_index integer := 0;
  v_citation_index integer;
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'execute_research' then return null; end if;
  select * into v_run from public.research_runs
  where id = (v_job.payload_json #>> '{input,researchRunId}')::uuid
    and user_id = v_job.user_id and job_id = v_job.id
  for update;
  if not found then return null; end if;
  if v_job.status = 'completed' then
    return case when v_run.output_note_id is null then null else jsonb_build_object(
      'jobId', v_job.id, 'researchRunId', v_run.id,
      'noteId', v_run.output_note_id, 'idempotent', true
    ) end;
  end if;
  if v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then return null; end if;
  if v_run.plan_json is null
     or p_model_provider is null or char_length(p_model_provider) not between 1 and 64
     or p_model_name is null or char_length(p_model_name) not between 1 and 128
     or p_prompt_version is null
     or p_prompt_version !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
     or p_output_hash is null or p_output_hash !~ '^[0-9a-f]{64}$'
     or p_title is null or char_length(p_title) not between 1 and 300
     or p_body is null or char_length(p_body) not between 1 and 100000
     or jsonb_typeof(p_findings) is distinct from 'array'
     or jsonb_array_length(p_findings) not between 1 and 100 then
    raise invalid_parameter_value using message = 'research draft publication is invalid';
  end if;

  -- Revalidate every original Evidence citation before creating the draft.
  for v_finding in select value from jsonb_array_elements(p_findings) loop
    if jsonb_typeof(v_finding) is distinct from 'object'
       or v_finding ->> 'text' is null
       or char_length(v_finding ->> 'text') not between 1 and 8000
       or v_finding ->> 'uncertainty' is null
       or char_length(v_finding ->> 'uncertainty') not between 1 and 2000
       or jsonb_typeof(v_finding -> 'citations') is distinct from 'array'
       or jsonb_array_length(v_finding -> 'citations') not between 1 and 20 then
      raise invalid_parameter_value using message = 'research finding is invalid';
    end if;
    for v_citation in select value from jsonb_array_elements(v_finding -> 'citations') loop
      if v_citation ->> 'evidenceId' is null
         or (v_citation ->> 'evidenceId') !~
           '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
         or v_citation ->> 'quote' is null
         or char_length(v_citation ->> 'quote') not between 1 and 4000
         or v_citation ->> 'relationType' not in ('supports','contradicts','contextualizes') then
        raise invalid_parameter_value using message = 'research citation is invalid';
      end if;
      select e.* into v_evidence
      from public.asset_evidence e
      join public.asset_revisions r
        on r.id = e.revision_id and r.user_id = e.user_id and r.status = 'validated'
      join public.assets a
        on a.id = r.asset_id and a.user_id = r.user_id
        and a.active_revision_id = r.id and a.status = 'ready'
      where e.id = (v_citation ->> 'evidenceId')::uuid
        and e.user_id = v_job.user_id;
      if not found or position((v_citation ->> 'quote') in v_evidence.excerpt) = 0 then
        raise integrity_constraint_violation
          using message = 'research citation is not supported by active original evidence';
      end if;
    end loop;
  end loop;

  insert into public.notes (user_id, note_type, title, body, publication_state)
  values (v_job.user_id, 'research_note', p_title, p_body, 'draft')
  returning id into v_note_id;

  v_finding_index := 0;
  for v_finding in select value from jsonb_array_elements(p_findings) loop
    v_citation_index := 0;
    for v_citation in select value from jsonb_array_elements(v_finding -> 'citations') loop
      select e.* into strict v_evidence
      from public.asset_evidence e
      where e.id = (v_citation ->> 'evidenceId')::uuid and e.user_id = v_job.user_id;
      select r.asset_id into strict v_asset_id
      from public.asset_revisions r
      where r.id = v_evidence.revision_id and r.user_id = v_job.user_id;
      insert into public.research_citations (
        user_id, research_run_id, note_id, finding_index, citation_index,
        relation_type, evidence_id, evidence_id_snapshot,
        asset_id, asset_id_snapshot, source_revision_id,
        source_revision_id_snapshot, locator_json, supporting_quote
      ) values (
        v_job.user_id, v_run.id, v_note_id, v_finding_index, v_citation_index,
        v_citation ->> 'relationType', v_evidence.id, v_evidence.id,
        v_asset_id, v_asset_id, v_evidence.revision_id,
        v_evidence.revision_id, v_evidence.locator_json, v_citation ->> 'quote'
      );
      insert into public.note_assets (user_id, note_id, asset_id, relationship)
      values (v_job.user_id, v_note_id, v_asset_id, 'context')
      on conflict (note_id, asset_id) do nothing;
      v_citation_index := v_citation_index + 1;
    end loop;
    v_finding_index := v_finding_index + 1;
  end loop;

  update public.research_runs set
    status = 'draft_ready', output_note_id = v_note_id,
    model_provider = p_model_provider, model_name = p_model_name,
    prompt_version = p_prompt_version, output_hash = p_output_hash
  where id = v_run.id;
  update public.job_attempts set ended_at = now(), outcome = 'completed'
  where job_id = v_job.id and lease_generation = p_lease_generation and ended_at is null;
  update public.jobs set status = 'completed', lease_owner = null,
    lease_expires_at = null, last_error_class = null,
    last_error_code = null, last_error_message = null
  where id = v_job.id;
  return jsonb_build_object(
    'jobId', v_job.id, 'researchRunId', v_run.id,
    'noteId', v_note_id, 'idempotent', false
  );
end;
$$;

revoke all on function public.start_research(text,uuid)
  from public, anon, authenticated, service_role;
revoke all on function public.get_research_context(uuid,text,bigint,jsonb)
  from public, anon, authenticated, service_role;
revoke all on function public.publish_research_draft(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) from public, anon, authenticated, service_role;
grant execute on function public.start_research(text,uuid) to authenticated;
grant execute on function public.get_research_context(uuid,text,bigint,jsonb)
  to service_role;
grant execute on function public.publish_research_draft(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) to service_role;
