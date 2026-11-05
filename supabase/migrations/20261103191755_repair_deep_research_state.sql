-- Deep Research infrastructure repair: persistent deadlines, operation hard
-- stops, truthful terminal states, and idempotent publication replay.

alter table public.research_runs
  drop constraint research_runs_status_check,
  add constraint research_runs_status_check check (
    status in (
      'queued', 'running', 'retry_wait', 'draft_ready', 'partial_ready',
      'insufficient', 'timed_out', 'failed', 'cancelled'
    )
  ),
  add column deadline_at timestamptz;

update public.research_runs
set deadline_at = created_at + make_interval(
  secs => least(
    greatest(
      coalesce((request_json #>> '{effectiveLimits,maxDurationSeconds}')::integer, 1800),
      30
    ),
    14400
  )
)
where research_mode = 'deep' and deadline_at is null;

alter table public.research_runs
  add constraint research_runs_deep_deadline_check check (
    (research_mode = 'legacy' and deadline_at is null)
    or (research_mode = 'deep' and deadline_at is not null)
  );

create or replace function public.set_deep_research_deadline()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if new.research_mode = 'deep' and new.deadline_at is null then
    new.deadline_at := coalesce(new.created_at, now()) + make_interval(
      secs => least(
        greatest(
          coalesce((new.request_json #>> '{effectiveLimits,maxDurationSeconds}')::integer, 1800),
          30
        ),
        14400
      )
    );
  end if;
  return new;
end;
$$;

revoke all on function public.set_deep_research_deadline()
  from public, anon, authenticated, service_role;

create trigger research_runs_set_deep_deadline
before insert on public.research_runs
for each row execute function public.set_deep_research_deadline();

create or replace function public.enforce_deep_research_operation_limits()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_run public.research_runs%rowtype;
  v_query_count integer;
  v_max_queries integer;
begin
  select * into strict v_run from public.research_runs
  where id = new.research_run_id for update;
  if v_run.research_mode <> 'deep' then
    raise integrity_constraint_violation using message = 'Deep operation requires a Deep Research run';
  end if;
  if v_run.deadline_at <= now() then
    raise check_violation using message = 'Deep Research duration is exhausted';
  end if;
  if new.action_type in ('search_internal', 'discover_web', 'fetch_web') then
    v_max_queries := least(
      greatest(coalesce((v_run.request_json #>> '{effectiveLimits,maxQueries}')::integer, 1), 1),
      50
    );
    select count(*) into v_query_count from public.research_operations
    where research_run_id = v_run.id
      and action_type in ('search_internal', 'discover_web', 'fetch_web');
    if v_query_count >= v_max_queries then
      raise check_violation using message = 'Deep Research query limit is exhausted';
    end if;
  end if;
  return new;
end;
$$;

revoke all on function public.enforce_deep_research_operation_limits()
  from public, anon, authenticated, service_role;

create trigger research_operations_enforce_deep_limits
before insert on public.research_operations
for each row execute function public.enforce_deep_research_operation_limits();

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
  if v_job.id is null or v_run.deadline_at <= now() then return false; end if;
  update public.research_operations set
    status = 'running', started_at = coalesce(started_at, now()), updated_at = now()
  where id = p_operation_id and research_run_id = v_run.id
    and lease_generation = p_lease_generation and status = 'reserved';
  return found;
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
    'limitUsd', limit_usd, 'protectedFinalReportReserveUsd', protected_final_reserve_usd,
    'reservedUsd', reserved_usd, 'actualUsd', actual_usd, 'uncertainUsd', uncertain_usd
  ) into v_budget from public.research_run_budgets where research_run_id = v_run.id;
  select coalesce(jsonb_agg(item order by sort_order, evidence_id), '[]'::jsonb)
  into v_evidence from (
    select 0 sort_order, rre.evidence_id, jsonb_build_object(
      'id', rre.evidence_id, 'assetId', rre.asset_id, 'revisionId', rre.revision_id,
      'excerpt', ae.excerpt, 'locator', ae.locator_json, 'sourceHash', ae.source_hash,
      'retrievalMethod', rre.retrieval_method, 'retrievalScore', rre.retrieval_score,
      'queryHits', rre.query_hits
    ) item
    from public.research_run_evidence rre
    join public.asset_evidence ae on ae.id = rre.evidence_id and ae.user_id = v_run.user_id
    where rre.research_run_id = v_run.id
    union all
    select 1, dwe.id, jsonb_strip_nulls(jsonb_build_object(
      'schemaVersion', 'evidence-record-v1', 'evidenceId', dwe.id,
      'runId', dwe.research_run_id, 'userId', dwe.user_id, 'origin', 'web',
      'sourceIdentity', dwe.canonical_url,
      'sourceMetadata', jsonb_build_object(
        'title', dwe.source_title, 'provider', dwe.provider, 'url', dwe.canonical_url
      ),
      'provenance', jsonb_build_object(
        'assetId', null, 'revisionId', null, 'parentEvidenceIds', '[]'::jsonb,
        'captureMethod', 'web_fetch'
      ),
      'excerpt', dwe.excerpt, 'citationLocator', dwe.locator_json,
      'contentHash', dwe.content_hash, 'capturedAt', dwe.captured_at,
      'sourcePublishedAt', dwe.source_published_at, 'storageScope', 'research_run',
      'citationEligible', dwe.citation_eligible,
      'validityStatus', dwe.validity_status, 'isSearchSnippet', dwe.is_search_snippet
    ))
    from public.deep_web_evidence dwe where dwe.research_run_id = v_run.id
  ) all_evidence;
  select coalesce(jsonb_agg(jsonb_build_object(
    'operationId', id, 'idempotencyKey', idempotency_key,
    'actionType', action_type, 'status', status, 'estimatedCostUsd', estimated_cost_usd,
    'actualCostUsd', actual_cost_usd, 'uncertainCostUsd', uncertain_cost_usd,
    'retryCount', retry_count
  ) order by created_at), '[]'::jsonb) into v_operations
  from public.research_operations where research_run_id = v_run.id;
  return jsonb_build_object(
    'request', v_run.request_json, 'plan', v_plan, 'state', v_state,
    'assessment', v_assessment, 'budget', v_budget,
    'evidence', v_evidence, 'operations', v_operations,
    'deadlineAt', v_run.deadline_at
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
  select result_status into v_result_status from public.deep_research_results
  where research_run_id = v_run.id;
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

-- Preserve the already-verified publication implementation and put an
-- idempotent, run-scoped replay boundary in front of it.
alter function public.publish_deep_research_draft(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) rename to publish_deep_research_draft_once;

revoke all on function public.publish_deep_research_draft_once(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) from public, anon, authenticated, service_role;
grant execute on function public.publish_deep_research_draft_once(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) to service_role;

create function public.publish_deep_research_draft(
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
  v_result public.deep_research_results%rowtype;
begin
  select * into v_job from public.jobs where id = p_job_id;
  if found and v_job.job_type = 'execute_research'
     and v_job.payload_json #>> '{input,researchMode}' = 'deep' then
    select * into v_run from public.research_runs
    where id = (v_job.payload_json #>> '{input,researchRunId}')::uuid
      and user_id = v_job.user_id and job_id = v_job.id and research_mode = 'deep';
  end if;
  if v_run.id is not null and v_job.status = 'completed' then
    select * into v_result from public.deep_research_results
    where research_run_id = v_run.id;
    if v_run.output_note_id is not null
       and v_run.status = 'draft_ready'
       and v_run.output_hash = p_output_hash
       and v_result.result_status = 'verified_complete'
       and v_result.output_hash = p_output_hash
       and v_result.published_note_id = v_run.output_note_id
       and exists (
         select 1 from public.job_attempts
         where job_id = v_job.id and lease_generation = p_lease_generation
           and worker_id = p_worker_id and outcome = 'completed'
       ) then
      return jsonb_build_object(
        'jobId', v_job.id, 'researchRunId', v_run.id,
        'noteId', v_run.output_note_id, 'idempotent', true
      );
    end if;
    raise integrity_constraint_violation
      using message = 'Deep Research publication replay conflicts with persisted output';
  end if;
  return public.publish_deep_research_draft_once(
    p_job_id, p_worker_id, p_lease_generation, p_model_provider, p_model_name,
    p_prompt_version, p_output_hash, p_title, p_body, p_findings
  );
end;
$$;

revoke all on function public.start_deep_research_operation(uuid,text,bigint,uuid)
  from public, anon, authenticated, service_role;
revoke all on function public.load_deep_research_state(uuid,text,bigint)
  from public, anon, authenticated, service_role;
revoke all on function public.complete_deep_research_without_publication(
  uuid,text,bigint,text,text
) from public, anon, authenticated, service_role;
revoke all on function public.publish_deep_research_draft(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) from public, anon, authenticated, service_role;
grant execute on function public.start_deep_research_operation(uuid,text,bigint,uuid)
  to service_role;
grant execute on function public.load_deep_research_state(uuid,text,bigint)
  to service_role;
grant execute on function public.complete_deep_research_without_publication(
  uuid,text,bigint,text,text
) to service_role;
grant execute on function public.publish_deep_research_draft(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) to service_role;
