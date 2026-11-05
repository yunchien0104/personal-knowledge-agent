-- Extend privacy-bounded, append-only attempt diagnostics to R2C without
-- changing existing R2B rows or its RPC signature.

alter table public.research_validation_diagnostics
  add column research_module text not null default 'r2b'
    check (research_module in ('r2b','r2c')),
  add column attempt_cost_usd numeric(8,4) not null default 0
    check (attempt_cost_usd >= 0),
  add column latency_ms integer not null default 0
    check (latency_ms >= 0);

create or replace function public.record_deep_research_validation_diagnostic(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_operation_id uuid,
  p_diagnostic jsonb
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
  v_row public.research_validation_diagnostics%rowtype;
  v_unexpected_keys text[];
  v_module text;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;

  select * into v_operation
  from public.research_operations
  where id = p_operation_id
    and research_run_id = v_run.id
    and job_id = v_job.id
    and lease_generation = p_lease_generation
    and status = 'running';
  if not found then return null; end if;

  v_module := case p_diagnostic ->> 'schemaVersion'
    when 'r2b-validation-diagnostic-v1' then 'r2b'
    when 'r2c-validation-diagnostic-v1' then 'r2c'
    else null
  end;
  if p_diagnostic is null or jsonb_typeof(p_diagnostic) <> 'object'
     or v_module is null or v_operation.cost_category <> v_module then
    raise invalid_parameter_value using message = 'validation diagnostic is invalid';
  end if;
  select array_agg(key order by key) into v_unexpected_keys
  from jsonb_object_keys(p_diagnostic) key
  where key not in (
    'schemaVersion','attemptNumber','attemptStage','requestId','finishReason',
    'inputTokens','outputTokens','reasoningTokens','costUsd','latencyMs',
    'validationStage','errorCode','fieldPath','ruleId','diagnosticSummary'
  );
  if v_unexpected_keys is not null then
    raise invalid_parameter_value using message = 'validation diagnostic has unexpected fields';
  end if;

  insert into public.research_validation_diagnostics (
    user_id, research_run_id, operation_id, job_id, lease_generation,
    attempt_number, attempt_stage, provider_request_id, finish_reason,
    input_tokens, output_tokens, reasoning_tokens, research_module,
    attempt_cost_usd, latency_ms, validation_stage,
    error_code, field_path, rule_id, diagnostic_summary
  ) values (
    v_run.user_id, v_run.id, v_operation.id, v_job.id, p_lease_generation,
    (p_diagnostic ->> 'attemptNumber')::integer,
    p_diagnostic ->> 'attemptStage', nullif(p_diagnostic ->> 'requestId',''),
    nullif(p_diagnostic ->> 'finishReason',''),
    coalesce((p_diagnostic ->> 'inputTokens')::integer,0),
    coalesce((p_diagnostic ->> 'outputTokens')::integer,0),
    coalesce((p_diagnostic ->> 'reasoningTokens')::integer,0),
    v_module, coalesce((p_diagnostic ->> 'costUsd')::numeric,0),
    coalesce((p_diagnostic ->> 'latencyMs')::integer,0),
    p_diagnostic ->> 'validationStage', nullif(p_diagnostic ->> 'errorCode',''),
    nullif(p_diagnostic ->> 'fieldPath',''), nullif(p_diagnostic ->> 'ruleId',''),
    p_diagnostic ->> 'diagnosticSummary'
  )
  on conflict (operation_id, attempt_stage) do nothing
  returning * into v_row;

  if v_row.id is null then
    select * into strict v_row
    from public.research_validation_diagnostics
    where operation_id = p_operation_id
      and attempt_stage = p_diagnostic ->> 'attemptStage';
    if v_row.attempt_number <> (p_diagnostic ->> 'attemptNumber')::integer
       or v_row.research_module <> v_module
       or v_row.validation_stage <> p_diagnostic ->> 'validationStage'
       or v_row.error_code is distinct from nullif(p_diagnostic ->> 'errorCode','')
       or v_row.field_path is distinct from nullif(p_diagnostic ->> 'fieldPath','')
       or v_row.rule_id is distinct from nullif(p_diagnostic ->> 'ruleId','')
       or v_row.diagnostic_summary <> p_diagnostic ->> 'diagnosticSummary'
       or v_row.provider_request_id is distinct from nullif(p_diagnostic ->> 'requestId','')
       or v_row.finish_reason is distinct from nullif(p_diagnostic ->> 'finishReason','')
       or v_row.input_tokens <> coalesce((p_diagnostic ->> 'inputTokens')::integer,0)
       or v_row.output_tokens <> coalesce((p_diagnostic ->> 'outputTokens')::integer,0)
       or v_row.reasoning_tokens <> coalesce((p_diagnostic ->> 'reasoningTokens')::integer,0)
       or v_row.attempt_cost_usd <> coalesce((p_diagnostic ->> 'costUsd')::numeric,0)
       or v_row.latency_ms <> coalesce((p_diagnostic ->> 'latencyMs')::integer,0) then
      raise integrity_constraint_violation using
        message = 'validation diagnostic attempt conflicts with durable record';
    end if;
  end if;

  return jsonb_build_object(
    'diagnosticId', v_row.id,
    'operationId', v_row.operation_id,
    'researchModule', v_row.research_module,
    'attemptNumber', v_row.attempt_number,
    'attemptStage', v_row.attempt_stage,
    'validationStage', v_row.validation_stage,
    'attemptCostUsd', v_row.attempt_cost_usd
  );
end;
$$;

create or replace function public.record_deep_research_evaluation_diagnostic(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_operation_id uuid,
  p_diagnostic jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if p_diagnostic is null or jsonb_typeof(p_diagnostic) <> 'object'
     or p_diagnostic ->> 'schemaVersion' <> 'r2c-validation-diagnostic-v1' then
    raise invalid_parameter_value using message = 'R2C validation diagnostic is invalid';
  end if;
  return public.record_deep_research_validation_diagnostic(
    p_job_id, p_worker_id, p_lease_generation, p_operation_id, p_diagnostic
  );
end;
$$;

revoke all on function public.record_deep_research_evaluation_diagnostic(
  uuid,text,bigint,uuid,jsonb
) from public, anon, authenticated;
grant execute on function public.record_deep_research_evaluation_diagnostic(
  uuid,text,bigint,uuid,jsonb
) to service_role;
