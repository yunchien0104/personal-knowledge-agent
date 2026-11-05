-- Deep Research Phase 4: durable checkpoints, operation state, cumulative
-- internal Evidence and transactionally enforced account/run budgets.

alter table public.deep_research_account_settings
  add column monthly_research_limit_usd numeric(8,4) not null default 10.0000
    check (monthly_research_limit_usd between 0 and 1000.0000);

alter table public.research_run_evidence
  drop constraint research_run_evidence_query_hits_check,
  add constraint research_run_evidence_query_hits_check
    check (query_hits between 1 and 50);

create table public.research_checkpoints (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  research_run_id uuid not null,
  job_id uuid not null references public.jobs (id) on delete cascade,
  lease_generation bigint not null check (lease_generation > 0),
  checkpoint_version integer not null check (checkpoint_version > 0),
  execution_phase text not null check (execution_phase in (
    'planning', 'retrieving', 'assessing', 'deciding', 'synthesizing',
    'verifying', 'publishing', 'terminal'
  )),
  plan_id uuid references public.research_plan_versions (id) on delete restrict,
  assessment_id uuid references public.research_assessments (id) on delete restrict,
  state_json jsonb not null check (
    jsonb_typeof(state_json) = 'object'
    and state_json ->> 'schemaVersion' = 'research-iteration-state-v1'
    and state_json ->> 'runId' = research_run_id::text
    and (state_json ->> 'checkpointVersion')::integer = checkpoint_version
    and state_json ->> 'executionPhase' = execution_phase
  ),
  created_at timestamptz not null default now(),
  constraint research_checkpoints_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint research_checkpoints_run_version_unique
    unique (research_run_id, checkpoint_version)
);

create index research_checkpoints_user_run_version_idx
  on public.research_checkpoints (user_id, research_run_id, checkpoint_version desc);

create table public.research_operations (
  id uuid primary key,
  user_id uuid not null,
  research_run_id uuid not null,
  job_id uuid not null references public.jobs (id) on delete cascade,
  lease_generation bigint not null check (lease_generation > 0),
  idempotency_key text not null check (char_length(idempotency_key) between 1 and 200),
  action_type text not null check (action_type in (
    'plan_research', 'search_internal', 'revise_query', 'evaluate_evidence',
    'discover_web', 'fetch_web', 'revise_plan', 'synthesize', 'verify', 'publish'
  )),
  cost_category text not null check (cost_category in (
    'r2b', 'r1', 'r2c', 'r2d', 'r2a', 'r2e', 'r2f', 'web'
  )),
  parameters_hash text not null check (parameters_hash ~ '^[0-9a-f]{64}$'),
  status text not null check (status in (
    'planned', 'reserved', 'running', 'completed', 'failed',
    'outcome_unknown', 'cancelled', 'abandoned'
  )),
  estimated_cost_usd numeric(8,4) not null check (estimated_cost_usd >= 0),
  reserved_cost_usd numeric(8,4) not null check (reserved_cost_usd >= 0),
  actual_cost_usd numeric(8,4) not null default 0 check (actual_cost_usd >= 0),
  uncertain_cost_usd numeric(8,4) not null default 0 check (uncertain_cost_usd >= 0),
  retry_count integer not null default 0 check (retry_count between 0 and 10),
  result_ref text check (result_ref is null or char_length(result_ref) between 1 and 1000),
  error_code text check (error_code is null or char_length(error_code) between 1 and 128),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  constraint research_operations_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint research_operations_run_key_unique
    unique (research_run_id, idempotency_key)
);

create index research_operations_user_run_status_idx
  on public.research_operations (user_id, research_run_id, status, created_at);

create table public.research_budget_periods (
  user_id uuid not null references auth.users (id) on delete cascade,
  period_start date not null,
  limit_usd numeric(8,4) not null check (limit_usd >= 0),
  reserved_usd numeric(8,4) not null default 0 check (reserved_usd >= 0),
  actual_usd numeric(8,4) not null default 0 check (actual_usd >= 0),
  uncertain_usd numeric(8,4) not null default 0 check (uncertain_usd >= 0),
  updated_at timestamptz not null default now(),
  primary key (user_id, period_start),
  constraint research_budget_periods_limit_check
    check (reserved_usd + actual_usd + uncertain_usd <= limit_usd)
);

create table public.research_run_budgets (
  research_run_id uuid primary key,
  user_id uuid not null,
  limit_usd numeric(8,4) not null check (limit_usd >= 0),
  protected_final_reserve_usd numeric(8,4) not null check (
    protected_final_reserve_usd >= 0 and protected_final_reserve_usd <= limit_usd
  ),
  reserved_usd numeric(8,4) not null default 0 check (reserved_usd >= 0),
  actual_usd numeric(8,4) not null default 0 check (actual_usd >= 0),
  uncertain_usd numeric(8,4) not null default 0 check (uncertain_usd >= 0),
  updated_at timestamptz not null default now(),
  constraint research_run_budgets_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint research_run_budgets_limit_check
    check (reserved_usd + actual_usd + uncertain_usd <= limit_usd)
);

create index research_run_budgets_user_idx
  on public.research_run_budgets (user_id, research_run_id);

create table public.research_budget_ledger (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  research_run_id uuid not null references public.research_run_budgets (research_run_id) on delete cascade,
  operation_id uuid not null references public.research_operations (id) on delete restrict,
  event_type text not null check (event_type in (
    'reserved', 'settled', 'released', 'uncertain'
  )),
  amount_usd numeric(8,4) not null check (amount_usd >= 0),
  cost_category text not null check (cost_category in (
    'r2b', 'r1', 'r2c', 'r2d', 'r2a', 'r2e', 'r2f', 'web'
  )),
  created_at timestamptz not null default now()
);

create index research_budget_ledger_user_run_idx
  on public.research_budget_ledger (user_id, research_run_id, id);

create table public.deep_research_evidence_rounds (
  user_id uuid not null,
  research_run_id uuid not null,
  evidence_id uuid not null,
  round_number integer not null check (round_number between 0 and 10),
  operation_id uuid not null references public.research_operations (id) on delete restrict,
  query_fingerprint text not null check (query_fingerprint ~ '^[0-9a-f]{64}$'),
  first_seen_at timestamptz not null default now(),
  primary key (research_run_id, evidence_id, round_number, operation_id),
  constraint deep_research_evidence_rounds_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint deep_research_evidence_rounds_evidence_fk
    foreign key (research_run_id, evidence_id)
    references public.research_run_evidence (research_run_id, evidence_id)
    on delete cascade
);

create index deep_research_evidence_rounds_user_run_idx
  on public.deep_research_evidence_rounds (user_id, research_run_id, round_number);

alter table public.research_checkpoints enable row level security;
alter table public.research_checkpoints force row level security;
alter table public.research_operations enable row level security;
alter table public.research_operations force row level security;
alter table public.research_budget_periods enable row level security;
alter table public.research_budget_periods force row level security;
alter table public.research_run_budgets enable row level security;
alter table public.research_run_budgets force row level security;
alter table public.research_budget_ledger enable row level security;
alter table public.research_budget_ledger force row level security;
alter table public.deep_research_evidence_rounds enable row level security;
alter table public.deep_research_evidence_rounds force row level security;

create policy research_checkpoints_select_own on public.research_checkpoints
for select to authenticated using ((select auth.uid()) = user_id);
create policy research_operations_select_own on public.research_operations
for select to authenticated using ((select auth.uid()) = user_id);
create policy research_budget_periods_select_own on public.research_budget_periods
for select to authenticated using ((select auth.uid()) = user_id);
create policy research_run_budgets_select_own on public.research_run_budgets
for select to authenticated using ((select auth.uid()) = user_id);
create policy research_budget_ledger_select_own on public.research_budget_ledger
for select to authenticated using ((select auth.uid()) = user_id);
create policy deep_research_evidence_rounds_select_own on public.deep_research_evidence_rounds
for select to authenticated using ((select auth.uid()) = user_id);

revoke all on public.research_checkpoints, public.research_operations,
  public.research_budget_periods, public.research_run_budgets,
  public.research_budget_ledger, public.deep_research_evidence_rounds
  from anon, authenticated;
grant select on public.research_checkpoints, public.research_operations,
  public.research_budget_periods, public.research_run_budgets,
  public.research_budget_ledger, public.deep_research_evidence_rounds
  to authenticated;
grant select, insert, update, delete on public.research_checkpoints,
  public.research_operations, public.research_budget_periods,
  public.research_run_budgets, public.deep_research_evidence_rounds to service_role;
grant select, insert on public.research_budget_ledger to service_role;
grant usage, select on sequence public.research_budget_ledger_id_seq to service_role;

create or replace function public.reject_research_budget_ledger_mutation()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  raise integrity_constraint_violation using message = 'research budget ledger is append-only';
end;
$$;

revoke all on function public.reject_research_budget_ledger_mutation()
  from public, anon, authenticated, service_role;
create trigger research_budget_ledger_append_only
before update or delete on public.research_budget_ledger
for each row execute function public.reject_research_budget_ledger_mutation();

create or replace function public.deep_research_job_context(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint
)
returns table (
  job_row public.jobs,
  run_row public.research_runs
)
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
     or v_job.lease_expires_at <= now()
     or v_job.payload_json #>> '{input,researchMode}' <> 'deep' then
    return;
  end if;
  select * into v_run from public.research_runs
  where id = (v_job.payload_json #>> '{input,researchRunId}')::uuid
    and user_id = v_job.user_id and job_id = v_job.id and research_mode = 'deep';
  if not found then return; end if;
  job_row := v_job;
  run_row := v_run;
  return next;
end;
$$;

revoke all on function public.deep_research_job_context(uuid,text,bigint)
  from public, anon, authenticated, service_role;
grant execute on function public.deep_research_job_context(uuid,text,bigint)
  to service_role;

create or replace function public.reserve_deep_research_operation(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_operation_id uuid,
  p_idempotency_key text,
  p_action_type text,
  p_cost_category text,
  p_parameters_hash text,
  p_estimated_cost_usd numeric
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_settings public.deep_research_account_settings%rowtype;
  v_period public.research_budget_periods%rowtype;
  v_budget public.research_run_budgets%rowtype;
  v_operation public.research_operations%rowtype;
  v_period_start date := date_trunc('month', now() at time zone 'Asia/Taipei')::date;
  v_run_limit numeric(8,4);
  v_reserve numeric(8,4);
  v_is_final boolean;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  if p_operation_id is null or p_idempotency_key is null
     or char_length(p_idempotency_key) not between 1 and 200
     or p_parameters_hash !~ '^[0-9a-f]{64}$'
     or p_action_type not in (
       'plan_research', 'search_internal', 'revise_query', 'evaluate_evidence',
       'discover_web', 'fetch_web', 'revise_plan', 'synthesize', 'verify', 'publish'
     ) or p_cost_category not in ('r2b','r1','r2c','r2d','r2a','r2e','r2f','web')
     or p_estimated_cost_usd is null or p_estimated_cost_usd < 0
     or p_estimated_cost_usd > 100 then
    raise invalid_parameter_value using message = 'research operation reservation is invalid';
  end if;

  select * into v_operation from public.research_operations
  where research_run_id = v_run.id and idempotency_key = p_idempotency_key
  for update;
  if found then
    if v_operation.id <> p_operation_id
       or v_operation.action_type <> p_action_type
       or v_operation.cost_category <> p_cost_category
       or v_operation.parameters_hash <> p_parameters_hash
       or v_operation.estimated_cost_usd <> p_estimated_cost_usd then
      raise integrity_constraint_violation using message = 'operation idempotency key conflicts';
    end if;
    if v_operation.status in ('reserved', 'running')
       and v_operation.lease_generation <> p_lease_generation then
      if v_operation.estimated_cost_usd = 0 then
        update public.research_operations set
          lease_generation = p_lease_generation,
          status = 'reserved',
          retry_count = retry_count + 1,
          updated_at = now()
        where id = v_operation.id returning * into v_operation;
      elsif v_operation.status = 'running' then
        update public.research_budget_periods set
          reserved_usd = reserved_usd - v_operation.reserved_cost_usd,
          uncertain_usd = uncertain_usd + v_operation.reserved_cost_usd,
          updated_at = now()
        where user_id = v_run.user_id and period_start = v_period_start;
        update public.research_run_budgets set
          reserved_usd = reserved_usd - v_operation.reserved_cost_usd,
          uncertain_usd = uncertain_usd + v_operation.reserved_cost_usd,
          updated_at = now()
        where research_run_id = v_run.id;
        update public.research_operations set
          status = 'outcome_unknown', reserved_cost_usd = 0,
          uncertain_cost_usd = v_operation.estimated_cost_usd,
          updated_at = now(), completed_at = now()
        where id = v_operation.id returning * into v_operation;
        insert into public.research_budget_ledger (
          user_id, research_run_id, operation_id, event_type, amount_usd, cost_category
        ) values (
          v_run.user_id, v_run.id, v_operation.id, 'uncertain',
          v_operation.estimated_cost_usd, v_operation.cost_category
        );
      end if;
    end if;
    return jsonb_build_object(
      'operationId', v_operation.id, 'status', v_operation.status,
      'reservedCostUsd', v_operation.reserved_cost_usd,
      'retryCount', v_operation.retry_count
    );
  end if;

  select * into v_settings from public.deep_research_account_settings
  where user_id = v_run.user_id;
  if not found then
    v_settings.max_cost_usd := 0.5000;
    v_settings.monthly_research_limit_usd := 10.0000;
  end if;
  v_run_limit := least(
    coalesce((v_run.request_json #>> '{effectiveLimits,maxCostUsd}')::numeric, 0.5000),
    v_settings.max_cost_usd
  );
  v_reserve := least(0.1000, v_run_limit);
  insert into public.research_budget_periods (
    user_id, period_start, limit_usd
  ) values (
    v_run.user_id, v_period_start, v_settings.monthly_research_limit_usd
  ) on conflict (user_id, period_start) do nothing;
  insert into public.research_run_budgets (
    research_run_id, user_id, limit_usd, protected_final_reserve_usd
  ) values (
    v_run.id, v_run.user_id, v_run_limit, v_reserve
  ) on conflict (research_run_id) do nothing;

  select * into strict v_period from public.research_budget_periods
  where user_id = v_run.user_id and period_start = v_period_start for update;
  select * into strict v_budget from public.research_run_budgets
  where research_run_id = v_run.id for update;
  v_is_final := p_cost_category in ('r2a', 'r2e', 'r2f');
  if v_period.reserved_usd + v_period.actual_usd + v_period.uncertain_usd
       + p_estimated_cost_usd > v_period.limit_usd
     or v_budget.reserved_usd + v_budget.actual_usd + v_budget.uncertain_usd
       + p_estimated_cost_usd
       + (case when v_is_final then 0 else v_budget.protected_final_reserve_usd end)
       > v_budget.limit_usd then
    return jsonb_build_object('status', 'budget_exhausted');
  end if;

  update public.research_budget_periods set
    reserved_usd = reserved_usd + p_estimated_cost_usd, updated_at = now()
  where user_id = v_run.user_id and period_start = v_period_start;
  update public.research_run_budgets set
    reserved_usd = reserved_usd + p_estimated_cost_usd, updated_at = now()
  where research_run_id = v_run.id;
  insert into public.research_operations (
    id, user_id, research_run_id, job_id, lease_generation,
    idempotency_key, action_type, cost_category, parameters_hash, status,
    estimated_cost_usd, reserved_cost_usd
  ) values (
    p_operation_id, v_run.user_id, v_run.id, v_job.id, p_lease_generation,
    p_idempotency_key, p_action_type, p_cost_category, p_parameters_hash,
    'reserved', p_estimated_cost_usd, p_estimated_cost_usd
  ) returning * into v_operation;
  insert into public.research_budget_ledger (
    user_id, research_run_id, operation_id, event_type, amount_usd, cost_category
  ) values (
    v_run.user_id, v_run.id, v_operation.id, 'reserved',
    p_estimated_cost_usd, p_cost_category
  );
  return jsonb_build_object(
    'operationId', v_operation.id, 'status', v_operation.status,
    'reservedCostUsd', v_operation.reserved_cost_usd, 'retryCount', 0
  );
end;
$$;

create or replace function public.start_deep_research_operation(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_operation_id uuid
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return false; end if;
  update public.research_operations set
    status = 'running', started_at = coalesce(started_at, now()), updated_at = now()
  where id = p_operation_id and research_run_id = v_run.id
    and lease_generation = p_lease_generation and status = 'reserved';
  return found;
end;
$$;

create or replace function public.settle_deep_research_operation(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_operation_id uuid,
  p_status text,
  p_actual_cost_usd numeric,
  p_result_ref text default null,
  p_error_code text default null
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_operation public.research_operations%rowtype;
  v_period_start date := date_trunc('month', now() at time zone 'Asia/Taipei')::date;
  v_uncertain numeric(8,4) := 0;
  v_actual numeric(8,4) := 0;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  if p_status not in ('completed','failed','outcome_unknown','cancelled','abandoned')
     or p_actual_cost_usd is null or p_actual_cost_usd < 0
     or p_actual_cost_usd > 100 then
    raise invalid_parameter_value using message = 'research operation settlement is invalid';
  end if;
  select * into v_operation from public.research_operations
  where id = p_operation_id and research_run_id = v_run.id for update;
  if not found or v_operation.lease_generation <> p_lease_generation then return null; end if;
  if v_operation.status in ('completed','failed','outcome_unknown','cancelled','abandoned') then
    return jsonb_build_object(
      'operationId', v_operation.id, 'status', v_operation.status,
      'actualCostUsd', v_operation.actual_cost_usd,
      'uncertainCostUsd', v_operation.uncertain_cost_usd
    );
  end if;
  if v_operation.status not in ('reserved','running') then
    raise object_not_in_prerequisite_state using message = 'operation cannot be settled';
  end if;
  if p_actual_cost_usd > v_operation.estimated_cost_usd then
    raise check_violation using message = 'actual operation cost exceeds its atomic reservation';
  end if;
  if p_status = 'outcome_unknown' then
    v_uncertain := greatest(v_operation.estimated_cost_usd, p_actual_cost_usd);
  else
    v_actual := p_actual_cost_usd;
  end if;
  update public.research_budget_periods set
    reserved_usd = reserved_usd - v_operation.reserved_cost_usd,
    actual_usd = actual_usd + v_actual,
    uncertain_usd = uncertain_usd + v_uncertain,
    updated_at = now()
  where user_id = v_run.user_id and period_start = v_period_start;
  update public.research_run_budgets set
    reserved_usd = reserved_usd - v_operation.reserved_cost_usd,
    actual_usd = actual_usd + v_actual,
    uncertain_usd = uncertain_usd + v_uncertain,
    updated_at = now()
  where research_run_id = v_run.id;
  update public.research_operations set
    status = p_status, reserved_cost_usd = 0,
    actual_cost_usd = v_actual, uncertain_cost_usd = v_uncertain,
    result_ref = p_result_ref, error_code = p_error_code,
    updated_at = now(), completed_at = now()
  where id = v_operation.id returning * into v_operation;
  insert into public.research_budget_ledger (
    user_id, research_run_id, operation_id, event_type, amount_usd, cost_category
  ) values (
    v_run.user_id, v_run.id, v_operation.id,
    case when p_status = 'outcome_unknown' then 'uncertain'
         when v_actual > 0 then 'settled' else 'released' end,
    case when p_status = 'outcome_unknown' then v_uncertain
         when v_actual > 0 then v_actual else v_operation.estimated_cost_usd end,
    v_operation.cost_category
  );
  return jsonb_build_object(
    'operationId', v_operation.id, 'status', v_operation.status,
    'actualCostUsd', v_actual, 'uncertainCostUsd', v_uncertain
  );
end;
$$;

create or replace function public.append_deep_research_evidence(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_operation_id uuid,
  p_round_number integer,
  p_query_fingerprint text,
  p_evidence jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_operation public.research_operations%rowtype;
  v_row jsonb;
  v_rank integer;
  v_added integer := 0;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  select * into v_operation from public.research_operations
  where id = p_operation_id and research_run_id = v_run.id
    and lease_generation = p_lease_generation and status = 'running';
  if not found then return null; end if;
  if p_round_number not between 0 and 10
     or p_query_fingerprint !~ '^[0-9a-f]{64}$'
     or jsonb_typeof(p_evidence) <> 'array'
     or jsonb_array_length(p_evidence) not between 1 and 12 then
    raise invalid_parameter_value using message = 'deep research Evidence checkpoint is invalid';
  end if;
  select coalesce(max(retrieval_rank), 0) into v_rank
  from public.research_run_evidence where research_run_id = v_run.id;
  for v_row in select value from jsonb_array_elements(p_evidence) loop
    if (v_row ->> 'id') is null or (v_row ->> 'assetId') is null
       or (v_row ->> 'revisionId') is null
       or (v_row ->> 'retrievalMethod') not in ('lexical','semantic','hybrid')
       or (v_row ->> 'retrievalScore')::double precision < 0
       or (v_row ->> 'queryHits')::integer not between 1 and 50 then
      raise invalid_parameter_value using message = 'deep research Evidence is invalid';
    end if;
    v_rank := least(v_rank + 1, 50);
    insert into public.research_run_evidence (
      user_id, research_run_id, evidence_id, asset_id, revision_id,
      retrieval_rank, retrieval_score, retrieval_method, query_hits
    ) values (
      v_run.user_id, v_run.id, (v_row ->> 'id')::uuid,
      (v_row ->> 'assetId')::uuid, (v_row ->> 'revisionId')::uuid,
      v_rank, (v_row ->> 'retrievalScore')::double precision,
      v_row ->> 'retrievalMethod', (v_row ->> 'queryHits')::integer
    ) on conflict (research_run_id, evidence_id) do update set
      retrieval_score = greatest(
        public.research_run_evidence.retrieval_score, excluded.retrieval_score
      ),
      retrieval_method = case
        when public.research_run_evidence.retrieval_method = excluded.retrieval_method
          then excluded.retrieval_method else 'hybrid' end,
      query_hits = least(
        50, public.research_run_evidence.query_hits + excluded.query_hits
      );
    insert into public.deep_research_evidence_rounds (
      user_id, research_run_id, evidence_id, round_number,
      operation_id, query_fingerprint
    ) values (
      v_run.user_id, v_run.id, (v_row ->> 'id')::uuid, p_round_number,
      p_operation_id, p_query_fingerprint
    ) on conflict do nothing;
    v_added := v_added + 1;
  end loop;
  return jsonb_build_object(
    'researchRunId', v_run.id, 'roundNumber', p_round_number,
    'processedEvidence', v_added,
    'totalEvidence', (select count(*) from public.research_run_evidence where research_run_id = v_run.id)
  );
end;
$$;

create or replace function public.commit_deep_research_checkpoint(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_expected_checkpoint_version integer,
  p_state jsonb,
  p_plan_id uuid default null,
  p_assessment_id uuid default null
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_current integer;
  v_next integer;
  v_checkpoint_id uuid;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  select coalesce(max(checkpoint_version), 0) into v_current
  from public.research_checkpoints where research_run_id = v_run.id;
  if v_current <> p_expected_checkpoint_version then
    raise serialization_failure using message = 'research checkpoint version conflict';
  end if;
  v_next := v_current + 1;
  if jsonb_typeof(p_state) <> 'object'
     or p_state ->> 'schemaVersion' <> 'research-iteration-state-v1'
     or p_state ->> 'runId' <> v_run.id::text
     or (p_state ->> 'checkpointVersion')::integer <> v_next then
    raise invalid_parameter_value using message = 'research checkpoint state is invalid';
  end if;
  insert into public.research_checkpoints (
    user_id, research_run_id, job_id, lease_generation, checkpoint_version,
    execution_phase, plan_id, assessment_id, state_json
  ) values (
    v_run.user_id, v_run.id, v_job.id, p_lease_generation, v_next,
    p_state ->> 'executionPhase', p_plan_id, p_assessment_id, p_state
  ) returning id into v_checkpoint_id;
  update public.jobs set checkpoint_ref = v_checkpoint_id::text
  where id = v_job.id and lease_owner = p_worker_id
    and lease_generation = p_lease_generation and status = 'running';
  if not found then raise serialization_failure using message = 'Worker lease was lost'; end if;
  update public.research_runs set progress_json = jsonb_build_object(
    'stage', p_state ->> 'executionPhase', 'checkpointVersion', v_next,
    'currentRound', (p_state ->> 'currentRound')::integer, 'updatedAt', now()
  ) where id = v_run.id;
  return jsonb_build_object(
    'checkpointId', v_checkpoint_id, 'checkpointVersion', v_next
  );
end;
$$;

create or replace function public.load_deep_research_state(
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
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_plan jsonb;
  v_state jsonb;
  v_assessment jsonb;
  v_budget jsonb;
  v_evidence jsonb;
  v_operations jsonb;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  select plan_json into v_plan from public.research_plan_versions
  where research_run_id = v_run.id order by plan_version desc limit 1;
  select state_json into v_state from public.research_checkpoints
  where research_run_id = v_run.id order by checkpoint_version desc limit 1;
  select assessment_json into v_assessment from public.research_assessments
  where research_run_id = v_run.id order by created_at desc limit 1;
  select jsonb_build_object(
    'limitUsd', limit_usd,
    'protectedFinalReportReserveUsd', protected_final_reserve_usd,
    'reservedUsd', reserved_usd,
    'actualUsd', actual_usd,
    'uncertainUsd', uncertain_usd
  ) into v_budget from public.research_run_budgets where research_run_id = v_run.id;
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', rre.evidence_id,
    'assetId', rre.asset_id,
    'revisionId', rre.revision_id,
    'excerpt', ae.excerpt,
    'locator', ae.locator_json,
    'sourceHash', ae.source_hash,
    'retrievalMethod', rre.retrieval_method,
    'retrievalScore', rre.retrieval_score,
    'queryHits', rre.query_hits
  ) order by rre.retrieval_rank), '[]'::jsonb) into v_evidence
  from public.research_run_evidence rre
  join public.asset_evidence ae on ae.id = rre.evidence_id and ae.user_id = v_run.user_id
  where rre.research_run_id = v_run.id;
  select coalesce(jsonb_agg(jsonb_build_object(
    'operationId', id, 'idempotencyKey', idempotency_key,
    'actionType', action_type, 'status', status,
    'estimatedCostUsd', estimated_cost_usd,
    'actualCostUsd', actual_cost_usd,
    'uncertainCostUsd', uncertain_cost_usd,
    'retryCount', retry_count
  ) order by created_at), '[]'::jsonb) into v_operations
  from public.research_operations where research_run_id = v_run.id;
  return jsonb_build_object(
    'request', v_run.request_json,
    'plan', v_plan,
    'state', v_state,
    'assessment', v_assessment,
    'budget', v_budget,
    'evidence', v_evidence,
    'operations', v_operations
  );
end;
$$;

create or replace function public.complete_deep_research_without_publication(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_outcome text,
  p_stop_reason text
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  if p_outcome not in ('qualified_partial','insufficient')
     or p_stop_reason not in (
       'quality_reached','diminishing_returns','budget_exhausted','max_rounds_reached',
       'duration_exhausted','cancelled','authorization_blocked','no_permitted_action'
     ) then
    raise invalid_parameter_value using message = 'deep research terminal outcome is invalid';
  end if;
  update public.research_runs set
    status = case when p_outcome = 'qualified_partial' then 'draft_ready' else 'failed' end,
    progress_json = progress_json || jsonb_build_object(
      'stage', 'terminal', 'outcome', p_outcome, 'stopReason', p_stop_reason,
      'published', false, 'updatedAt', now()
    )
  where id = v_run.id;
  update public.jobs set
    status = 'completed', lease_owner = null, lease_expires_at = null,
    checkpoint_ref = v_run.id::text,
    last_error_class = null,
    last_error_code = case when p_outcome = 'insufficient' then 'insufficient_research' else null end,
    last_error_message = null
  where id = v_job.id and lease_owner = p_worker_id
    and lease_generation = p_lease_generation and status = 'running';
  if not found then return null; end if;
  update public.job_attempts set ended_at = now()
  where job_id = v_job.id and lease_generation = p_lease_generation and ended_at is null;
  return jsonb_build_object(
    'researchRunId', v_run.id, 'status', 'completed',
    'outcome', p_outcome, 'published', false
  );
end;
$$;

revoke all on function public.reserve_deep_research_operation(
  uuid,text,bigint,uuid,text,text,text,text,numeric
) from public, anon, authenticated, service_role;
revoke all on function public.start_deep_research_operation(uuid,text,bigint,uuid)
  from public, anon, authenticated, service_role;
revoke all on function public.settle_deep_research_operation(
  uuid,text,bigint,uuid,text,numeric,text,text
) from public, anon, authenticated, service_role;
revoke all on function public.append_deep_research_evidence(
  uuid,text,bigint,uuid,integer,text,jsonb
) from public, anon, authenticated, service_role;
revoke all on function public.commit_deep_research_checkpoint(
  uuid,text,bigint,integer,jsonb,uuid,uuid
) from public, anon, authenticated, service_role;
revoke all on function public.load_deep_research_state(uuid,text,bigint)
  from public, anon, authenticated, service_role;
revoke all on function public.complete_deep_research_without_publication(
  uuid,text,bigint,text,text
) from public, anon, authenticated, service_role;

grant execute on function public.reserve_deep_research_operation(
  uuid,text,bigint,uuid,text,text,text,text,numeric
) to service_role;
grant execute on function public.start_deep_research_operation(uuid,text,bigint,uuid)
  to service_role;
grant execute on function public.settle_deep_research_operation(
  uuid,text,bigint,uuid,text,numeric,text,text
) to service_role;
grant execute on function public.append_deep_research_evidence(
  uuid,text,bigint,uuid,integer,text,jsonb
) to service_role;
grant execute on function public.commit_deep_research_checkpoint(
  uuid,text,bigint,integer,jsonb,uuid,uuid
) to service_role;
grant execute on function public.load_deep_research_state(uuid,text,bigint)
  to service_role;
grant execute on function public.complete_deep_research_without_publication(
  uuid,text,bigint,text,text
) to service_role;
