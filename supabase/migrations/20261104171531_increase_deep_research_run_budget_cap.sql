-- Raise only the Deep Research per-run hard cap from US$0.50 to US$1.00.
-- Daily (US$2.00), monthly (US$10.00), final-stage protection, atomic
-- reservations, lease fencing, and all automatic/opportunity budgets remain
-- unchanged.

alter table public.deep_research_account_settings
  drop constraint deep_research_account_settings_max_cost_usd_check;

alter table public.deep_research_account_settings
  alter column max_cost_usd set default 1.0000,
  add constraint deep_research_account_settings_max_cost_usd_check
    check (max_cost_usd between 0 and 1.0000);

-- Upgrade only accounts that still use the former product default. Preserve
-- intentionally lower account-specific limits.
update public.deep_research_account_settings
set max_cost_usd = 1.0000,
    updated_at = now()
where max_cost_usd = 0.5000;

create or replace function public.start_deep_research(
  p_question text,
  p_request_key uuid,
  p_scope_asset_ids uuid[] default null,
  p_web_policy text default 'suggest',
  p_requested_limits jsonb default '{}'::jsonb,
  p_analysis_spec jsonb default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_question text := btrim(p_question);
  v_run_id uuid := gen_random_uuid();
  v_request_id uuid := gen_random_uuid();
  v_job_id uuid := gen_random_uuid();
  v_run public.research_runs%rowtype;
  v_settings public.deep_research_account_settings%rowtype;
  v_limits jsonb;
  v_permissions jsonb;
  v_request jsonb;
  v_key text := 'deep-research:' || p_request_key::text;
  v_requested_rounds integer;
  v_requested_queries integer;
  v_requested_cost numeric(8,4);
  v_requested_duration integer;
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  if v_question is null or char_length(v_question) not between 3 and 500
     or p_request_key is null
     or p_web_policy not in ('disabled', 'suggest', 'auto')
     or jsonb_typeof(coalesce(p_requested_limits, '{}'::jsonb)) <> 'object'
     or coalesce(p_requested_limits, '{}'::jsonb) - array[
       'maxRounds', 'maxQueries', 'maxCostUsd', 'maxDurationSeconds'
     ] <> '{}'::jsonb
     or (p_analysis_spec is not null and (
       jsonb_typeof(p_analysis_spec) <> 'object'
       or p_analysis_spec ->> 'schemaVersion' <> 'analysis-spec-v1'
     ))
     or (p_scope_asset_ids is not null and cardinality(p_scope_asset_ids) not between 1 and 100)
     or (p_scope_asset_ids is not null and (
       select count(*) <> count(distinct requested.id)
       from unnest(p_scope_asset_ids) requested(id)
     )) then
    raise invalid_parameter_value using message = 'deep research request is invalid';
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

  select * into v_settings from public.deep_research_account_settings
  where user_id = v_user_id;
  if not found then
    v_settings.user_id := v_user_id;
    v_settings.web_auto_authorized := false;
    v_settings.max_rounds := 3;
    v_settings.max_queries := 12;
    v_settings.max_cost_usd := 1.0000;
    v_settings.max_duration_seconds := 1800;
  end if;
  if p_web_policy = 'auto' and not v_settings.web_auto_authorized then
    raise insufficient_privilege using message = 'automatic web research is not authorized';
  end if;

  begin
    v_requested_rounds := coalesce((p_requested_limits ->> 'maxRounds')::integer, v_settings.max_rounds);
    v_requested_queries := coalesce((p_requested_limits ->> 'maxQueries')::integer, v_settings.max_queries);
    v_requested_cost := coalesce((p_requested_limits ->> 'maxCostUsd')::numeric, v_settings.max_cost_usd);
    v_requested_duration := coalesce((p_requested_limits ->> 'maxDurationSeconds')::integer, v_settings.max_duration_seconds);
  exception when invalid_text_representation or numeric_value_out_of_range then
    raise invalid_parameter_value using message = 'deep research limits are invalid';
  end;
  if v_requested_rounds < 1 or v_requested_queries < 1 or v_requested_cost < 0
     or v_requested_duration < 30 then
    raise invalid_parameter_value using message = 'deep research limits are invalid';
  end if;
  v_limits := jsonb_build_object(
    'maxRounds', least(v_requested_rounds, v_settings.max_rounds, 10),
    'maxQueries', least(v_requested_queries, v_settings.max_queries, 50),
    'maxCostUsd', least(v_requested_cost, v_settings.max_cost_usd, 1.0000),
    'maxDurationSeconds', least(v_requested_duration, v_settings.max_duration_seconds, 14400)
  );
  v_permissions := jsonb_build_object(
    'internalKnowledge', true,
    'webDiscovery', p_web_policy in ('suggest', 'auto'),
    'webFetch', p_web_policy = 'auto' and v_settings.web_auto_authorized
  );
  v_request := jsonb_strip_nulls(jsonb_build_object(
    'schemaVersion', 'research-request-v1',
    'requestId', v_request_id,
    'runId', v_run_id,
    'idempotencyKey', p_request_key,
    'userId', v_user_id,
    'objective', v_question,
    'originalQuestion', v_question,
    'executionMode', 'deep',
    'internalKnowledgeScope', jsonb_build_object('selectedAssetIds', p_scope_asset_ids),
    'webPolicy', p_web_policy,
    'permissions', v_permissions,
    'effectiveLimits', v_limits,
    'analysisSpec', p_analysis_spec
  ));

  insert into public.research_runs (
    id, request_id, user_id, request_key, question, scope_asset_ids,
    research_mode, web_policy, request_json
  ) values (
    v_run_id, v_request_id, v_user_id, p_request_key, v_question,
    p_scope_asset_ids, 'deep', p_web_policy, v_request
  ) on conflict (user_id, request_key) do nothing;

  select * into strict v_run from public.research_runs
  where user_id = v_user_id and request_key = p_request_key for update;
  if v_run.research_mode <> 'deep' or v_run.question <> v_question
     or v_run.scope_asset_ids is distinct from p_scope_asset_ids
     or v_run.web_policy <> p_web_policy then
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
        'input', jsonb_build_object(
          'researchRunId', v_run.id,
          'question', v_run.question,
          'scopeAssetIds', v_run.scope_asset_ids,
          'researchMode', 'deep',
          'researchRequest', v_run.request_json
        )
      ),
      1, v_key, 'research', 120
    );
    update public.research_runs set job_id = v_job_id
    where id = v_run.id returning * into v_run;
  end if;

  return jsonb_build_object(
    'id', v_run.id,
    'requestId', v_run.request_id,
    'jobId', v_run.job_id,
    'status', v_run.status,
    'mode', v_run.research_mode,
    'effectiveLimits', v_run.request_json -> 'effectiveLimits'
  );
end;
$$;

revoke all on function public.start_deep_research(text,uuid,uuid[],text,jsonb,jsonb)
  from public, anon, authenticated, service_role;
grant execute on function public.start_deep_research(text,uuid,uuid[],text,jsonb,jsonb)
  to authenticated;

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
    v_settings.max_cost_usd := 1.0000;
    v_settings.daily_research_limit_usd := 2.0000;
    v_settings.monthly_research_limit_usd := 10.0000;
  end if;
  v_run_limit := least(
    coalesce((v_run.request_json #>> '{effectiveLimits,maxCostUsd}')::numeric, 1.0000),
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

revoke all on function public.reserve_deep_research_operation(
  uuid,text,bigint,uuid,text,text,text,text,numeric
) from public, anon, authenticated;
grant execute on function public.reserve_deep_research_operation(
  uuid,text,bigint,uuid,text,text,text,text,numeric
) to service_role;

create or replace function public.ensure_deep_research_final_reserve(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_required_reserve_usd numeric
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
  v_budget public.research_run_budgets%rowtype;
  v_run_limit numeric(8,4);
begin
  select * into v_context
  from public.deep_research_job_context(
    p_job_id, p_worker_id, p_lease_generation
  );
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;

  if p_required_reserve_usd is null
     or p_required_reserve_usd < 0
     or p_required_reserve_usd > 100 then
    raise invalid_parameter_value using
      message = 'required finalization reserve is invalid';
  end if;

  select * into v_settings
  from public.deep_research_account_settings
  where user_id = v_run.user_id;
  if not found then
    v_settings.max_cost_usd := 1.0000;
  end if;
  v_run_limit := least(
    coalesce(
      (v_run.request_json #>> '{effectiveLimits,maxCostUsd}')::numeric,
      1.0000
    ),
    v_settings.max_cost_usd
  );

  if p_required_reserve_usd > v_run_limit then
    return jsonb_build_object(
      'status', 'budget_exhausted',
      'limitUsd', v_run_limit,
      'requiredFinalReserveUsd', p_required_reserve_usd
    );
  end if;

  insert into public.research_run_budgets (
    research_run_id, user_id, limit_usd, protected_final_reserve_usd
  ) values (
    v_run.id, v_run.user_id, v_run_limit, p_required_reserve_usd
  ) on conflict (research_run_id) do nothing;

  select * into strict v_budget
  from public.research_run_budgets
  where research_run_id = v_run.id
  for update;

  if v_budget.reserved_usd + v_budget.actual_usd
       + v_budget.uncertain_usd
       + greatest(
           v_budget.protected_final_reserve_usd,
           p_required_reserve_usd
         ) > v_budget.limit_usd then
    return jsonb_build_object(
      'status', 'budget_exhausted',
      'limitUsd', v_budget.limit_usd,
      'reservedUsd', v_budget.reserved_usd,
      'actualUsd', v_budget.actual_usd,
      'uncertainUsd', v_budget.uncertain_usd,
      'requiredFinalReserveUsd', p_required_reserve_usd
    );
  end if;

  update public.research_run_budgets
  set protected_final_reserve_usd = greatest(
        protected_final_reserve_usd,
        p_required_reserve_usd
      ),
      updated_at = now()
  where research_run_id = v_run.id
  returning * into v_budget;

  return jsonb_build_object(
    'status', 'protected',
    'limitUsd', v_budget.limit_usd,
    'protectedFinalReportReserveUsd',
      v_budget.protected_final_reserve_usd,
    'reservedUsd', v_budget.reserved_usd,
    'actualUsd', v_budget.actual_usd,
    'uncertainUsd', v_budget.uncertain_usd
  );
end;
$$;

revoke all on function public.ensure_deep_research_final_reserve(
  uuid,text,bigint,numeric
) from public, anon, authenticated;
grant execute on function public.ensure_deep_research_final_reserve(
  uuid,text,bigint,numeric
) to service_role;
