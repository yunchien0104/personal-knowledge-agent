-- Durable, privacy-bounded validation diagnostics for R2B provider attempts.
-- Raw prompts, evidence, model output, and credentials are intentionally absent.

create table public.research_validation_diagnostics (
  id bigint generated always as identity primary key,
  user_id uuid not null,
  research_run_id uuid not null,
  operation_id uuid not null references public.research_operations (id) on delete cascade,
  job_id uuid not null references public.jobs (id) on delete cascade,
  lease_generation bigint not null check (lease_generation > 0),
  attempt_number integer not null check (attempt_number between 1 and 3),
  attempt_stage text not null check (attempt_stage in ('initial','repair','fallback')),
  provider_request_id text check (
    provider_request_id is null or (
      char_length(provider_request_id) between 1 and 200
      and provider_request_id !~ '[^ -~]'
    )
  ),
  finish_reason text check (
    finish_reason is null or (
      char_length(finish_reason) between 1 and 64
      and finish_reason !~ '[^ -~]'
    )
  ),
  input_tokens integer not null default 0 check (input_tokens >= 0),
  output_tokens integer not null default 0 check (output_tokens >= 0),
  reasoning_tokens integer not null default 0 check (reasoning_tokens >= 0),
  validation_stage text not null check (validation_stage in (
    'provider_terminal','provider_dto','backend_contract','quality_gate','accepted','fallback'
  )),
  error_code text check (error_code is null or error_code ~ '^[a-z0-9_]{1,64}$'),
  field_path text check (
    field_path is null or (
      char_length(field_path) between 1 and 300 and field_path !~ '[^ -~]'
    )
  ),
  rule_id text check (rule_id is null or rule_id ~ '^[a-z0-9_.-]{1,128}$'),
  diagnostic_summary text not null check (
    char_length(diagnostic_summary) between 1 and 500
    and diagnostic_summary !~ '[^ -~]'
  ),
  created_at timestamptz not null default now(),
  constraint research_validation_diagnostics_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint research_validation_diagnostics_attempt_number_stage_check check (
    (attempt_number = 1 and attempt_stage = 'initial')
    or (attempt_number = 2 and attempt_stage = 'repair')
    or (attempt_number = 3 and attempt_stage = 'fallback')
  ),
  constraint research_validation_diagnostics_operation_stage_unique
    unique (operation_id, attempt_stage)
);

create index research_validation_diagnostics_user_run_operation_idx
  on public.research_validation_diagnostics (
    user_id, research_run_id, operation_id, attempt_number
  );

alter table public.research_validation_diagnostics enable row level security;
alter table public.research_validation_diagnostics force row level security;

create policy research_validation_diagnostics_select_own
on public.research_validation_diagnostics
for select to authenticated
using ((select auth.uid()) = user_id);

revoke all on public.research_validation_diagnostics from public, anon, authenticated;
grant select on public.research_validation_diagnostics to authenticated;
grant select, insert on public.research_validation_diagnostics to service_role;
grant usage, select on sequence public.research_validation_diagnostics_id_seq to service_role;

create or replace function public.reject_research_validation_diagnostic_mutation()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  raise integrity_constraint_violation using
    message = 'research validation diagnostics are append-only';
end;
$$;

revoke all on function public.reject_research_validation_diagnostic_mutation()
  from public, anon, authenticated, service_role;

create trigger research_validation_diagnostics_append_only
before update or delete on public.research_validation_diagnostics
for each row execute function public.reject_research_validation_diagnostic_mutation();

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

  if p_diagnostic is null or jsonb_typeof(p_diagnostic) <> 'object'
     or p_diagnostic ->> 'schemaVersion' <> 'r2b-validation-diagnostic-v1' then
    raise invalid_parameter_value using message = 'validation diagnostic is invalid';
  end if;
  select array_agg(key order by key) into v_unexpected_keys
  from jsonb_object_keys(p_diagnostic) key
  where key not in (
    'schemaVersion','attemptNumber','attemptStage','requestId','finishReason',
    'inputTokens','outputTokens','reasoningTokens','validationStage','errorCode',
    'fieldPath','ruleId','diagnosticSummary'
  );
  if v_unexpected_keys is not null then
    raise invalid_parameter_value using message = 'validation diagnostic has unexpected fields';
  end if;

  insert into public.research_validation_diagnostics (
    user_id, research_run_id, operation_id, job_id, lease_generation,
    attempt_number, attempt_stage, provider_request_id, finish_reason,
    input_tokens, output_tokens, reasoning_tokens, validation_stage,
    error_code, field_path, rule_id, diagnostic_summary
  ) values (
    v_run.user_id, v_run.id, v_operation.id, v_job.id, p_lease_generation,
    (p_diagnostic ->> 'attemptNumber')::integer,
    p_diagnostic ->> 'attemptStage', nullif(p_diagnostic ->> 'requestId',''),
    nullif(p_diagnostic ->> 'finishReason',''),
    coalesce((p_diagnostic ->> 'inputTokens')::integer,0),
    coalesce((p_diagnostic ->> 'outputTokens')::integer,0),
    coalesce((p_diagnostic ->> 'reasoningTokens')::integer,0),
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
       or v_row.validation_stage <> p_diagnostic ->> 'validationStage'
       or v_row.error_code is distinct from nullif(p_diagnostic ->> 'errorCode','')
       or v_row.field_path is distinct from nullif(p_diagnostic ->> 'fieldPath','')
       or v_row.rule_id is distinct from nullif(p_diagnostic ->> 'ruleId','')
       or v_row.diagnostic_summary <> p_diagnostic ->> 'diagnosticSummary'
       or v_row.provider_request_id is distinct from nullif(p_diagnostic ->> 'requestId','')
       or v_row.finish_reason is distinct from nullif(p_diagnostic ->> 'finishReason','')
       or v_row.input_tokens <> coalesce((p_diagnostic ->> 'inputTokens')::integer,0)
       or v_row.output_tokens <> coalesce((p_diagnostic ->> 'outputTokens')::integer,0)
       or v_row.reasoning_tokens <> coalesce((p_diagnostic ->> 'reasoningTokens')::integer,0) then
      raise integrity_constraint_violation using
        message = 'validation diagnostic attempt conflicts with durable record';
    end if;
  end if;

  return jsonb_build_object(
    'diagnosticId', v_row.id,
    'operationId', v_row.operation_id,
    'attemptNumber', v_row.attempt_number,
    'attemptStage', v_row.attempt_stage,
    'validationStage', v_row.validation_stage
  );
end;
$$;

revoke all on function public.record_deep_research_validation_diagnostic(
  uuid,text,bigint,uuid,jsonb
) from public, anon, authenticated;
grant execute on function public.record_deep_research_validation_diagnostic(
  uuid,text,bigint,uuid,jsonb
) to service_role;
