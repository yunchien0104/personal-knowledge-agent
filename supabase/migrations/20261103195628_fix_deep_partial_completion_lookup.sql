-- Keep the persisted result lookup unambiguous and prevent the generic Job sync
-- trigger from overwriting a truthful Deep terminal status after completion.

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
  v_result_status text;
  v_run_status text;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  if p_outcome not in ('qualified_partial','insufficient')
     or p_stop_reason not in (
       'quality_reached','diminishing_returns','budget_exhausted','max_rounds_reached',
       'max_queries_exhausted','duration_exhausted','cancelled',
       'authorization_blocked','no_permitted_action'
     ) then
    raise invalid_parameter_value using message = 'deep research terminal outcome is invalid';
  end if;
  select drr.result_status into v_result_status
  from public.deep_research_results as drr
  where drr.research_run_id = v_run.id;
  v_run_status := case
    when v_result_status = 'verified_partial' then 'partial_ready'
    when p_stop_reason = 'duration_exhausted' then 'timed_out'
    when p_outcome = 'insufficient' then 'insufficient'
    else 'failed'
  end;
  update public.research_runs set
    status = v_run_status,
    progress_json = progress_json || jsonb_build_object(
      'stage', 'terminal', 'outcome', p_outcome, 'stopReason', p_stop_reason,
      'resultStatus', v_result_status, 'published', false, 'updatedAt', now()
    )
  where id = v_run.id;
  update public.jobs set
    status = 'completed', lease_owner = null, lease_expires_at = null,
    checkpoint_ref = v_run.id::text,
    last_error_class = null,
    last_error_code = case
      when v_result_status = 'verified_partial' then null
      when p_stop_reason = 'duration_exhausted' then 'research_timed_out'
      when p_outcome = 'insufficient' then 'insufficient_research'
      else 'unverified_partial_research'
    end,
    last_error_message = null
  where id = v_job.id and lease_owner = p_worker_id
    and lease_generation = p_lease_generation and status = 'running';
  if not found then return null; end if;
  update public.job_attempts set ended_at = now(), outcome = 'completed'
  where job_id = v_job.id and lease_generation = p_lease_generation and ended_at is null;
  return jsonb_build_object(
    'researchRunId', v_run.id, 'status', 'completed',
    'runStatus', v_run_status, 'outcome', p_outcome,
    'resultStatus', v_result_status, 'published', false
  );
end;
$$;

revoke all on function public.complete_deep_research_without_publication(
  uuid,text,bigint,text,text
) from public, anon, authenticated, service_role;
grant execute on function public.complete_deep_research_without_publication(
  uuid,text,bigint,text,text
) to service_role;

-- Preserve truthful Deep terminal states when the generic Job status trigger
-- observes completion without a Note. Legacy Research keeps its prior mapping.
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
      when new.status = 'completed'
        and research_mode = 'deep'
        and status in ('partial_ready','insufficient','timed_out','failed','cancelled')
        then status
      when new.status = 'completed' then 'failed'
      else status
    end
    where job_id = new.id;
  end if;
  return new;
end;
$$;

revoke all on function public.sync_research_run_job_status()
  from public, anon, authenticated, service_role;
