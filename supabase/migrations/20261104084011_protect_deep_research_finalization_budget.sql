-- Protect the configured worst-case Deep finalization envelope before any
-- non-final paid operation. The amount is supplied by the Worker from verified
-- model pricing and token/claim ceilings; existing hard run/daily/monthly caps
-- remain authoritative.

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
    v_settings.max_cost_usd := 0.5000;
  end if;
  v_run_limit := least(
    coalesce(
      (v_run.request_json #>> '{effectiveLimits,maxCostUsd}')::numeric,
      0.5000
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
