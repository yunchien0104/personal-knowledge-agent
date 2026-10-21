-- Step 7A draft: deterministic, suggestion-only Research Opportunities.
-- This migration is intentionally not deployed by this task. It reuses the
-- durable queue and never creates a Research Run or performs a paid model call.

alter table public.jobs drop constraint jobs_resource_class_check;
alter table public.jobs add constraint jobs_resource_class_check
  check (resource_class in ('lightweight', 'media', 'llm', 'research', 'opportunity'));

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
    or (
      asset_id is null
      and job_type = 'rank_opportunities'
      and not (payload_json -> 'input') ? 'assetId'
      and (payload_json -> 'input') ? 'rankingDate'
    )
  )
);

alter table public.job_attempts drop constraint job_attempts_outcome_check;
alter table public.job_attempts add constraint job_attempts_outcome_check check (
  outcome is null or outcome in (
    'completed', 'retry', 'failed', 'lease_expired', 'cancelled', 'paused_budget'
  )
);

create table public.research_opportunities (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  question text not null check (char_length(question) between 3 and 500),
  rationale text not null check (char_length(rationale) between 1 and 2000),
  score smallint not null check (score between 60 and 100),
  status text not null default 'new' check (
    status in ('new', 'saved', 'snoozed', 'dismissed', 'started', 'completed')
  ),
  signal_types text[] not null check (cardinality(signal_types) between 1 and 10),
  topic_ids uuid[] not null default '{}',
  evidence_ids uuid[] not null default '{}',
  semantic_key text not null check (semantic_key ~ '^[0-9a-f]{64}$'),
  cooldown_until timestamptz,
  latest_evidence_at timestamptz not null,
  recommended_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint research_opportunities_id_user_unique unique (id, user_id),
  constraint research_opportunities_user_semantic_unique unique (user_id, semantic_key)
);

create index research_opportunities_user_status_score_idx
  on public.research_opportunities (user_id, status, score desc, recommended_at desc);
create index research_opportunities_user_cooldown_idx
  on public.research_opportunities (user_id, cooldown_until);

create table public.opportunity_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  opportunity_id uuid not null,
  event_type text not null check (
    event_type in (
      'ranked', 'recommended', 'saved', 'skip', 'not_interested', 'started', 'completed'
    )
  ),
  metadata jsonb not null default '{}' check (jsonb_typeof(metadata) = 'object'),
  automatic_cost_usd numeric(8,6) not null default 0 check (automatic_cost_usd >= 0),
  created_at timestamptz not null default now(),
  constraint opportunity_events_opportunity_owner_fk
    foreign key (opportunity_id, user_id)
    references public.research_opportunities (id, user_id)
    on delete cascade
);

create index opportunity_events_user_created_idx
  on public.opportunity_events (user_id, created_at desc);
create index opportunity_events_opportunity_created_idx
  on public.opportunity_events (opportunity_id, created_at desc);

create table public.automatic_budget_periods (
  user_id uuid not null references auth.users (id) on delete cascade,
  period_date date not null,
  spent_usd numeric(8,6) not null default 0 check (spent_usd between 0 and 0.500000),
  updated_at timestamptz not null default now(),
  primary key (user_id, period_date)
);

create trigger research_opportunities_set_updated_at
before update on public.research_opportunities
for each row execute function public.set_current_timestamp_updated_at();

alter table public.research_opportunities enable row level security;
alter table public.research_opportunities force row level security;
alter table public.opportunity_events enable row level security;
alter table public.opportunity_events force row level security;
alter table public.automatic_budget_periods enable row level security;
alter table public.automatic_budget_periods force row level security;

create policy research_opportunities_select_own on public.research_opportunities
for select to authenticated using ((select auth.uid()) = user_id);
create policy opportunity_events_select_own on public.opportunity_events
for select to authenticated using ((select auth.uid()) = user_id);
create policy automatic_budget_periods_select_own on public.automatic_budget_periods
for select to authenticated using ((select auth.uid()) = user_id);

revoke all on public.research_opportunities, public.opportunity_events,
  public.automatic_budget_periods from anon, authenticated;
grant select on public.research_opportunities, public.opportunity_events,
  public.automatic_budget_periods to authenticated;
grant select, insert, update, delete on public.research_opportunities,
  public.opportunity_events, public.automatic_budget_periods to service_role;

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
     and p_resource_class not in ('lightweight', 'media', 'llm', 'research', 'opportunity') then
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

create or replace function public.enqueue_opportunity_ranking(
  p_user_id uuid,
  p_ranking_date date
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job_id uuid := gen_random_uuid();
  v_key text;
  v_job public.jobs%rowtype;
begin
  if p_user_id is null or p_ranking_date is null
     or not exists (select 1 from auth.users where id = p_user_id) then
    raise invalid_parameter_value using message = 'ranking request is invalid';
  end if;
  v_key := 'opportunity-rank:' || p_ranking_date::text;
  insert into public.jobs (
    id, user_id, asset_id, job_type, payload_json, payload_schema_version,
    idempotency_key, resource_class, priority, max_attempts
  ) values (
    v_job_id, p_user_id, null, 'rank_opportunities',
    jsonb_build_object(
      'schemaVersion', 1,
      'jobId', v_job_id,
      'userId', p_user_id,
      'jobType', 'rank_opportunities',
      'idempotencyKey', v_key,
      'input', jsonb_build_object(
        'rankingDate', p_ranking_date,
        'timezone', 'Asia/Taipei',
        'minimumScore', 60,
        'maxNew', 10,
        'maxAutomaticCostUsd', '0.50'
      )
    ),
    1, v_key, 'opportunity', 20, 3
  ) on conflict (user_id, idempotency_key) do nothing;

  select * into strict v_job from public.jobs
  where user_id = p_user_id and idempotency_key = v_key;
  if v_job.job_type <> 'rank_opportunities' then
    raise integrity_constraint_violation using message = 'ranking idempotency key conflicts';
  end if;
  return jsonb_build_object('jobId', v_job.id, 'status', v_job.status);
end;
$$;

create or replace function public.enqueue_due_opportunity_rankings()
returns integer
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_user_id uuid;
  v_ranking_date date := (now() at time zone 'Asia/Taipei')::date;
  v_eligible integer := 0;
begin
  for v_user_id in
    select distinct c.user_id
    from public.claims c
    join public.notes n
      on n.id = c.note_id and n.user_id = c.user_id
      and n.note_type = 'source_note'
    order by c.user_id
  loop
    perform public.enqueue_opportunity_ranking(v_user_id, v_ranking_date);
    v_eligible := v_eligible + 1;
  end loop;
  return v_eligible;
end;
$$;

create or replace function public.get_opportunity_context(
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
  v_date date;
  v_signals jsonb;
  v_existing jsonb;
  v_completed jsonb;
  v_spent numeric(8,6);
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'rank_opportunities'
     or v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then
    return null;
  end if;
  v_date := (v_job.payload_json #>> '{input,rankingDate}')::date;

  with claim_signals as (
    select
      c.id,
      c.user_id,
      c.text,
      c.claim_type,
      c.verification_status,
      c.needs_review,
      max(greatest(c.created_at, e.created_at)) as observed_at,
      array_agg(distinct e.id order by e.id) as evidence_ids
    from public.claims c
    join public.notes n
      on n.id = c.note_id and n.user_id = c.user_id and n.note_type = 'source_note'
    join public.claim_evidence ce
      on ce.claim_id = c.id and ce.user_id = c.user_id
    join public.asset_evidence e
      on e.id = ce.evidence_id and e.user_id = c.user_id
    join public.asset_revisions r
      on r.id = e.revision_id and r.user_id = e.user_id and r.status = 'validated'
    join public.assets a
      on a.id = r.asset_id and a.user_id = r.user_id
      and a.active_revision_id = r.id and a.status = 'ready'
    where c.user_id = v_job.user_id
    group by c.id, c.user_id, c.text, c.claim_type,
      c.verification_status, c.needs_review
    order by observed_at desc, c.id
    limit 100
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', id,
    'userId', user_id,
    'signalType', 'claim_gap',
    'text', text,
    'claimType', claim_type,
    'verificationStatus', verification_status,
    'needsReview', needs_review,
    'evidenceIds', evidence_ids,
    'observedAt', observed_at
  ) order by observed_at desc, id), '[]'::jsonb)
  into v_signals from claim_signals;

  select coalesce(jsonb_agg(jsonb_build_object(
    'semanticKey', semantic_key,
    'cooldownUntil', cooldown_until,
    'status', status
  ) order by semantic_key), '[]'::jsonb)
  into v_existing from public.research_opportunities
  where user_id = v_job.user_id;

  select coalesce(jsonb_agg(question order by created_at, id), '[]'::jsonb)
  into v_completed from public.research_runs
  where user_id = v_job.user_id and status = 'draft_ready';

  select coalesce(spent_usd, 0) into v_spent
  from public.automatic_budget_periods
  where user_id = v_job.user_id and period_date = v_date;

  return jsonb_build_object(
    'userId', v_job.user_id,
    'rankingDate', v_date,
    'signals', v_signals,
    'existingOpportunities', v_existing,
    'completedQuestions', v_completed,
    'automaticSpentUsd', coalesce(v_spent, 0)::text,
    'estimatedRunCostUsd', '0'
  );
end;
$$;

create or replace function public.pause_opportunity_budget(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
begin
  update public.jobs set
    status = 'paused_budget', lease_owner = null, lease_expires_at = null,
    last_error_class = null, last_error_code = 'automatic_budget_exhausted',
    last_error_message = 'Daily automatic processing budget is exhausted.'
  where id = p_job_id and job_type = 'rank_opportunities' and status = 'running'
    and lease_owner = p_worker_id and lease_generation = p_lease_generation
    and lease_expires_at > now();
  if not found then return false; end if;
  update public.job_attempts set
    ended_at = now(), heartbeat_at = now(), outcome = 'paused_budget',
    error_code = 'automatic_budget_exhausted'
  where job_id = p_job_id and lease_generation = p_lease_generation
    and worker_id = p_worker_id and ended_at is null;
  return true;
end;
$$;

create or replace function public.publish_opportunity_ranking(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_candidates jsonb,
  p_estimated_cost_usd numeric
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_date date;
  v_period public.automatic_budget_periods%rowtype;
  v_candidate jsonb;
  v_existing public.research_opportunities%rowtype;
  v_opportunity_id uuid;
  v_daily_new integer;
  v_created integer := 0;
  v_updated integer := 0;
  v_evidence_ids uuid[];
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'rank_opportunities' then return null; end if;
  if v_job.status = 'completed' then
    return jsonb_build_object('created', 0, 'updated', 0, 'idempotent', true);
  end if;
  if v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then return null; end if;
  if jsonb_typeof(p_candidates) is distinct from 'array'
     or jsonb_array_length(p_candidates) > 100
     or p_estimated_cost_usd is null or p_estimated_cost_usd < 0 then
    raise invalid_parameter_value using message = 'opportunity ranking is invalid';
  end if;

  v_date := (v_job.payload_json #>> '{input,rankingDate}')::date;
  insert into public.automatic_budget_periods (user_id, period_date)
  values (v_job.user_id, v_date)
  on conflict (user_id, period_date) do nothing;
  select * into strict v_period from public.automatic_budget_periods
  where user_id = v_job.user_id and period_date = v_date for update;
  if v_period.spent_usd + p_estimated_cost_usd > 0.500000 then
    perform public.pause_opportunity_budget(
      p_job_id, p_worker_id, p_lease_generation
    );
    return jsonb_build_object('status', 'paused_budget');
  end if;

  select count(*) into v_daily_new from public.research_opportunities
  where user_id = v_job.user_id
    and (created_at at time zone 'Asia/Taipei')::date = v_date;

  for v_candidate in select value from jsonb_array_elements(p_candidates) loop
    if jsonb_typeof(v_candidate) is distinct from 'object'
       or v_candidate ->> 'question' is null
       or char_length(v_candidate ->> 'question') not between 3 and 500
       or v_candidate ->> 'rationale' is null
       or char_length(v_candidate ->> 'rationale') not between 1 and 2000
       or v_candidate ->> 'score' is null
       or (v_candidate ->> 'score')::integer not between 60 and 100
       or v_candidate ->> 'semanticKey' is null
       or v_candidate ->> 'semanticKey' !~ '^[0-9a-f]{64}$'
       or v_candidate ->> 'latestEvidenceAt' is null
       or jsonb_typeof(v_candidate -> 'signalTypes') is distinct from 'array'
       or jsonb_array_length(v_candidate -> 'signalTypes') not between 1 and 10
       or jsonb_typeof(v_candidate -> 'evidenceIds') is distinct from 'array' then
      raise invalid_parameter_value using message = 'opportunity candidate is invalid';
    end if;
    select coalesce(array_agg(value::uuid), '{}') into v_evidence_ids
    from jsonb_array_elements_text(v_candidate -> 'evidenceIds');
    if exists (
      select 1 from unnest(v_evidence_ids) evidence_id
      where not exists (
        select 1 from public.asset_evidence e
        join public.asset_revisions r
          on r.id = e.revision_id and r.user_id = e.user_id and r.status = 'validated'
        join public.assets a
          on a.id = r.asset_id and a.user_id = r.user_id
          and a.active_revision_id = r.id and a.status = 'ready'
        where e.id = evidence_id and e.user_id = v_job.user_id
      )
    ) then
      raise integrity_constraint_violation using message = 'opportunity evidence is invalid';
    end if;

    select * into v_existing from public.research_opportunities
    where user_id = v_job.user_id
      and semantic_key = v_candidate ->> 'semanticKey'
    for update;
    if found then
      if v_existing.cooldown_until is null or v_existing.cooldown_until <= now() then
        update public.research_opportunities set
          question = v_candidate ->> 'question',
          rationale = v_candidate ->> 'rationale',
          score = (v_candidate ->> 'score')::integer,
          signal_types = array(select jsonb_array_elements_text(v_candidate -> 'signalTypes')),
          evidence_ids = v_evidence_ids,
          latest_evidence_at = (v_candidate ->> 'latestEvidenceAt')::timestamptz,
          recommended_at = now(),
          status = case when status in ('snoozed', 'dismissed') then 'new' else status end,
          cooldown_until = case when cooldown_until <= now() then null else cooldown_until end
        where id = v_existing.id;
        v_opportunity_id := v_existing.id;
        v_updated := v_updated + 1;
        insert into public.opportunity_events (
          user_id, opportunity_id, event_type, metadata
        ) values (
          v_job.user_id, v_opportunity_id, 'ranked',
          jsonb_build_object('jobId', v_job.id, 'score', v_candidate -> 'score')
        );
      end if;
    elsif v_daily_new + v_created < 10 then
      insert into public.research_opportunities (
        user_id, question, rationale, score, signal_types, evidence_ids,
        semantic_key, latest_evidence_at
      ) values (
        v_job.user_id,
        v_candidate ->> 'question',
        v_candidate ->> 'rationale',
        (v_candidate ->> 'score')::integer,
        array(select jsonb_array_elements_text(v_candidate -> 'signalTypes')),
        v_evidence_ids,
        v_candidate ->> 'semanticKey',
        (v_candidate ->> 'latestEvidenceAt')::timestamptz
      ) returning id into v_opportunity_id;
      v_created := v_created + 1;
      insert into public.opportunity_events (
        user_id, opportunity_id, event_type, metadata
      ) values (
        v_job.user_id, v_opportunity_id, 'recommended',
        jsonb_build_object('jobId', v_job.id, 'score', v_candidate -> 'score')
      );
    end if;
  end loop;

  update public.automatic_budget_periods set
    spent_usd = spent_usd + p_estimated_cost_usd, updated_at = now()
  where user_id = v_job.user_id and period_date = v_date;
  update public.job_attempts set
    ended_at = now(), heartbeat_at = now(), outcome = 'completed'
  where job_id = v_job.id and lease_generation = p_lease_generation
    and worker_id = p_worker_id and ended_at is null;
  update public.jobs set
    status = 'completed', lease_owner = null, lease_expires_at = null,
    last_error_class = null, last_error_code = null, last_error_message = null
  where id = v_job.id;
  return jsonb_build_object(
    'created', v_created, 'updated', v_updated, 'idempotent', false
  );
end;
$$;

create or replace function public.record_opportunity_feedback(
  p_opportunity_id uuid,
  p_feedback text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_opportunity public.research_opportunities%rowtype;
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  if p_feedback not in ('saved', 'skip', 'not_interested', 'started', 'completed') then
    raise invalid_parameter_value using message = 'opportunity feedback is invalid';
  end if;
  select * into v_opportunity from public.research_opportunities
  where id = p_opportunity_id and user_id = v_user_id for update;
  if not found then raise no_data_found using message = 'opportunity not found'; end if;

  update public.research_opportunities set
    status = case p_feedback
      when 'saved' then 'saved'
      when 'skip' then 'snoozed'
      when 'not_interested' then 'dismissed'
      when 'started' then 'started'
      when 'completed' then 'completed'
    end,
    cooldown_until = case p_feedback
      when 'skip' then now() + interval '7 days'
      when 'not_interested' then now() + interval '30 days'
      when 'completed' then now() + interval '30 days'
      else null
    end
  where id = v_opportunity.id returning * into v_opportunity;
  insert into public.opportunity_events (user_id, opportunity_id, event_type)
  values (v_user_id, v_opportunity.id, p_feedback);

  -- Feedback only changes the suggestion lifecycle. It never creates a
  -- research_run or execute_research Job; that remains an explicit user action.
  return jsonb_build_object(
    'id', v_opportunity.id,
    'status', v_opportunity.status,
    'cooldownUntil', v_opportunity.cooldown_until
  );
end;
$$;

revoke execute on function public.enqueue_opportunity_ranking(uuid,date)
  from public, anon, authenticated, service_role;
revoke execute on function public.enqueue_due_opportunity_rankings()
  from public, anon, authenticated, service_role;
revoke execute on function public.get_opportunity_context(uuid,text,bigint)
  from public, anon, authenticated, service_role;
revoke execute on function public.pause_opportunity_budget(uuid,text,bigint)
  from public, anon, authenticated, service_role;
revoke execute on function public.publish_opportunity_ranking(
  uuid,text,bigint,jsonb,numeric
) from public, anon, authenticated, service_role;
revoke execute on function public.record_opportunity_feedback(uuid,text)
  from public, anon, authenticated, service_role;

grant execute on function public.enqueue_opportunity_ranking(uuid,date) to service_role;
grant execute on function public.enqueue_due_opportunity_rankings() to service_role;
grant execute on function public.get_opportunity_context(uuid,text,bigint) to service_role;
grant execute on function public.pause_opportunity_budget(uuid,text,bigint) to service_role;
grant execute on function public.publish_opportunity_ranking(
  uuid,text,bigint,jsonb,numeric
) to service_role;
grant execute on function public.record_opportunity_feedback(uuid,text) to authenticated;
