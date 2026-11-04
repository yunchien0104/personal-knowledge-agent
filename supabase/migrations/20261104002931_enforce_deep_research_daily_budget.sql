-- Enforce the approved aggregate daily Deep Research limit in the same
-- Asia/Taipei accounting timezone as the existing monthly budget.

alter table public.deep_research_account_settings
  add column daily_research_limit_usd numeric(8,4) not null default 2.0000
    check (daily_research_limit_usd between 0 and 100.0000);

create table public.research_daily_budgets (
  user_id uuid not null references auth.users (id) on delete cascade,
  budget_day date not null,
  limit_usd numeric(8,4) not null check (limit_usd >= 0),
  reserved_usd numeric(8,4) not null default 0 check (reserved_usd >= 0),
  actual_usd numeric(8,4) not null default 0 check (actual_usd >= 0),
  uncertain_usd numeric(8,4) not null default 0 check (uncertain_usd >= 0),
  updated_at timestamptz not null default now(),
  primary key (user_id, budget_day),
  constraint research_daily_budgets_limit_check
    check (reserved_usd + actual_usd + uncertain_usd <= limit_usd)
);

alter table public.research_operations add column reservation_day date;
update public.research_operations
set reservation_day = (created_at at time zone 'Asia/Taipei')::date
where reservation_day is null;
alter table public.research_operations
  alter column reservation_day set not null,
  alter column reservation_day set default
    (now() at time zone 'Asia/Taipei')::date;

insert into public.research_daily_budgets (
  user_id, budget_day, limit_usd, reserved_usd, actual_usd, uncertain_usd
)
select
  operations.user_id,
  operations.reservation_day,
  greatest(
    least(coalesce(settings.daily_research_limit_usd, 2.0000), 2.0000),
    sum(operations.reserved_cost_usd + operations.actual_cost_usd
      + operations.uncertain_cost_usd)
  ),
  sum(operations.reserved_cost_usd),
  sum(operations.actual_cost_usd),
  sum(operations.uncertain_cost_usd)
from public.research_operations as operations
left join public.deep_research_account_settings as settings
  on settings.user_id = operations.user_id
group by operations.user_id, operations.reservation_day,
  settings.daily_research_limit_usd;

alter table public.research_daily_budgets enable row level security;
alter table public.research_daily_budgets force row level security;
create policy research_daily_budgets_select_own
on public.research_daily_budgets for select to authenticated
using ((select auth.uid()) = user_id);
revoke all on public.research_daily_budgets from anon, authenticated;
grant select on public.research_daily_budgets to authenticated;
grant select, insert, update, delete on public.research_daily_budgets to service_role;

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
  v_daily public.research_daily_budgets%rowtype;
  v_budget public.research_run_budgets%rowtype;
  v_operation public.research_operations%rowtype;
  v_period_start date := date_trunc('month', now() at time zone 'Asia/Taipei')::date;
  v_budget_day date := (now() at time zone 'Asia/Taipei')::date;
  v_run_limit numeric(8,4);
  v_reserve numeric(8,4);
  v_is_final boolean;
  v_recovered boolean := false;
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
      if v_operation.status = 'reserved' then
        update public.research_operations set
          lease_generation = p_lease_generation,
          retry_count = retry_count + 1,
          updated_at = now()
        where id = v_operation.id returning * into v_operation;
        v_recovered := true;
      elsif v_operation.estimated_cost_usd = 0 then
        update public.research_operations set
          lease_generation = p_lease_generation,
          status = 'reserved',
          retry_count = retry_count + 1,
          started_at = null,
          updated_at = now()
        where id = v_operation.id returning * into v_operation;
        v_recovered := true;
      else
        update public.research_budget_periods set
          reserved_usd = reserved_usd - v_operation.reserved_cost_usd,
          uncertain_usd = uncertain_usd + v_operation.reserved_cost_usd,
          updated_at = now()
        where user_id = v_run.user_id
          and period_start = v_operation.reservation_period_start;
        update public.research_daily_budgets set
          reserved_usd = reserved_usd - v_operation.reserved_cost_usd,
          uncertain_usd = uncertain_usd + v_operation.reserved_cost_usd,
          updated_at = now()
        where user_id = v_run.user_id
          and budget_day = v_operation.reservation_day;
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
      'retryCount', v_operation.retry_count, 'recovered', v_recovered
    );
  end if;

  select * into v_settings from public.deep_research_account_settings
  where user_id = v_run.user_id;
  if not found then
    v_settings.max_cost_usd := 0.5000;
    v_settings.daily_research_limit_usd := 2.0000;
    v_settings.monthly_research_limit_usd := 10.0000;
  end if;
  v_run_limit := least(
    coalesce((v_run.request_json #>> '{effectiveLimits,maxCostUsd}')::numeric, 0.5000),
    v_settings.max_cost_usd
  );
  v_reserve := least(0.1000, v_run_limit);
  insert into public.research_budget_periods (user_id, period_start, limit_usd)
  values (v_run.user_id, v_period_start,
    least(v_settings.monthly_research_limit_usd, 10.0000))
  on conflict (user_id, period_start) do update set
    limit_usd = greatest(
      research_budget_periods.reserved_usd + research_budget_periods.actual_usd
        + research_budget_periods.uncertain_usd,
      least(excluded.limit_usd, 10.0000)
    );
  insert into public.research_daily_budgets (user_id, budget_day, limit_usd)
  values (v_run.user_id, v_budget_day,
    least(v_settings.daily_research_limit_usd, 2.0000))
  on conflict (user_id, budget_day) do update set
    limit_usd = greatest(
      research_daily_budgets.reserved_usd + research_daily_budgets.actual_usd
        + research_daily_budgets.uncertain_usd,
      least(excluded.limit_usd, 2.0000)
    );
  insert into public.research_run_budgets (
    research_run_id, user_id, limit_usd, protected_final_reserve_usd
  ) values (v_run.id, v_run.user_id, v_run_limit, v_reserve)
  on conflict (research_run_id) do nothing;

  select * into strict v_period from public.research_budget_periods
  where user_id = v_run.user_id and period_start = v_period_start for update;
  select * into strict v_daily from public.research_daily_budgets
  where user_id = v_run.user_id and budget_day = v_budget_day for update;
  select * into strict v_budget from public.research_run_budgets
  where research_run_id = v_run.id for update;
  v_is_final := p_cost_category in ('r2a', 'r2e', 'r2f');
  if v_period.reserved_usd + v_period.actual_usd + v_period.uncertain_usd
       + p_estimated_cost_usd > v_period.limit_usd
     or v_daily.reserved_usd + v_daily.actual_usd + v_daily.uncertain_usd
       + p_estimated_cost_usd > v_daily.limit_usd
     or v_budget.reserved_usd + v_budget.actual_usd + v_budget.uncertain_usd
       + p_estimated_cost_usd
       + (case when v_is_final then 0 else v_budget.protected_final_reserve_usd end)
       > v_budget.limit_usd then
    return jsonb_build_object('status', 'budget_exhausted');
  end if;

  update public.research_budget_periods set
    reserved_usd = reserved_usd + p_estimated_cost_usd, updated_at = now()
  where user_id = v_run.user_id and period_start = v_period_start;
  update public.research_daily_budgets set
    reserved_usd = reserved_usd + p_estimated_cost_usd, updated_at = now()
  where user_id = v_run.user_id and budget_day = v_budget_day;
  update public.research_run_budgets set
    reserved_usd = reserved_usd + p_estimated_cost_usd, updated_at = now()
  where research_run_id = v_run.id;
  insert into public.research_operations (
    id, user_id, research_run_id, job_id, lease_generation,
    idempotency_key, action_type, cost_category, parameters_hash, status,
    estimated_cost_usd, reserved_cost_usd, reservation_period_start, reservation_day
  ) values (
    p_operation_id, v_run.user_id, v_run.id, v_job.id, p_lease_generation,
    p_idempotency_key, p_action_type, p_cost_category, p_parameters_hash,
    'reserved', p_estimated_cost_usd, p_estimated_cost_usd, v_period_start,
    v_budget_day
  ) returning * into v_operation;
  insert into public.research_budget_ledger (
    user_id, research_run_id, operation_id, event_type, amount_usd, cost_category
  ) values (
    v_run.user_id, v_run.id, v_operation.id, 'reserved',
    p_estimated_cost_usd, p_cost_category
  );
  return jsonb_build_object(
    'operationId', v_operation.id, 'status', v_operation.status,
    'reservedCostUsd', v_operation.reserved_cost_usd,
    'retryCount', 0, 'recovered', false
  );
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
  v_uncertain numeric(8,4) := 0;
  v_actual numeric(8,4) := 0;
  v_released numeric(8,4) := 0;
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
      'uncertainCostUsd', v_operation.uncertain_cost_usd,
      'idempotent', true
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
    v_released := v_operation.reserved_cost_usd - v_actual;
  end if;
  update public.research_budget_periods set
    reserved_usd = reserved_usd - v_operation.reserved_cost_usd,
    actual_usd = actual_usd + v_actual,
    uncertain_usd = uncertain_usd + v_uncertain,
    updated_at = now()
  where user_id = v_run.user_id
    and period_start = v_operation.reservation_period_start;
  update public.research_daily_budgets set
    reserved_usd = reserved_usd - v_operation.reserved_cost_usd,
    actual_usd = actual_usd + v_actual,
    uncertain_usd = uncertain_usd + v_uncertain,
    updated_at = now()
  where user_id = v_run.user_id
    and budget_day = v_operation.reservation_day;
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
  if v_uncertain > 0 then
    insert into public.research_budget_ledger (
      user_id, research_run_id, operation_id, event_type, amount_usd, cost_category
    ) values (
      v_run.user_id, v_run.id, v_operation.id, 'uncertain',
      v_uncertain, v_operation.cost_category
    );
  else
    if v_actual > 0 then
      insert into public.research_budget_ledger (
        user_id, research_run_id, operation_id, event_type, amount_usd, cost_category
      ) values (
        v_run.user_id, v_run.id, v_operation.id, 'settled',
        v_actual, v_operation.cost_category
      );
    end if;
    if v_released > 0 then
      insert into public.research_budget_ledger (
        user_id, research_run_id, operation_id, event_type, amount_usd, cost_category
      ) values (
        v_run.user_id, v_run.id, v_operation.id, 'released',
        v_released, v_operation.cost_category
      );
    end if;
  end if;
  return jsonb_build_object(
    'operationId', v_operation.id, 'status', v_operation.status,
    'actualCostUsd', v_actual, 'uncertainCostUsd', v_uncertain,
    'releasedCostUsd', v_released, 'idempotent', false
  );
end;
$$;

revoke all on function public.reserve_deep_research_operation(
  uuid,text,bigint,uuid,text,text,text,text,numeric
) from public, anon, authenticated, service_role;
revoke all on function public.settle_deep_research_operation(
  uuid,text,bigint,uuid,text,numeric,text,text
) from public, anon, authenticated, service_role;
grant execute on function public.reserve_deep_research_operation(
  uuid,text,bigint,uuid,text,text,text,text,numeric
) to service_role;
grant execute on function public.settle_deep_research_operation(
  uuid,text,bigint,uuid,text,numeric,text,text
) to service_role;
