-- Additive Deep-only R2A/R2E/R2F paid-call artifacts. Legacy Research tables,
-- RPCs, and execution semantics remain unchanged.

alter table public.research_checkpoints
  drop constraint research_checkpoints_execution_phase_check,
  add constraint research_checkpoints_execution_phase_check check (
    execution_phase in (
      'planning','retrieving','assessing','deciding','synthesizing',
      'verifying','repairing','publishing','terminal'
    )
  );

create table public.research_finalization_artifacts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  research_run_id uuid not null,
  operation_id uuid not null unique
    references public.research_operations (id) on delete restrict,
  job_id uuid not null references public.jobs (id) on delete cascade,
  lease_generation bigint not null check (lease_generation > 0),
  artifact_type text not null check (artifact_type in (
    'draft','verification','repaired_draft','reverification'
  )),
  artifact_key text not null check (char_length(artifact_key) between 1 and 200),
  artifact_json jsonb not null check (
    jsonb_typeof(artifact_json) = 'object'
    and octet_length(artifact_json::text) <= 1048576
  ),
  output_hash text not null check (output_hash ~ '^[0-9a-f]{64}$'),
  provider text not null check (char_length(provider) between 1 and 100),
  model_name text not null check (char_length(model_name) between 1 and 200),
  request_id text check (request_id is null or char_length(request_id) between 1 and 200),
  finish_reason text check (finish_reason is null or char_length(finish_reason) between 1 and 100),
  input_tokens integer not null check (input_tokens >= 0),
  output_tokens integer not null check (output_tokens >= 0),
  reasoning_tokens integer not null check (reasoning_tokens >= 0),
  actual_cost_usd numeric(8,4) not null check (actual_cost_usd >= 0),
  latency_ms integer not null check (latency_ms >= 0),
  created_at timestamptz not null default now(),
  constraint research_finalization_artifacts_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint research_finalization_artifacts_run_key_unique
    unique (research_run_id, artifact_type, artifact_key)
);

create index research_finalization_artifacts_user_run_idx
  on public.research_finalization_artifacts (user_id, research_run_id, created_at);

alter table public.research_finalization_artifacts enable row level security;
alter table public.research_finalization_artifacts force row level security;

create policy research_finalization_artifacts_select_own
on public.research_finalization_artifacts
for select to authenticated
using ((select auth.uid()) = user_id);

revoke all on public.research_finalization_artifacts from public, anon, authenticated;
grant select on public.research_finalization_artifacts to authenticated;
grant select, insert on public.research_finalization_artifacts to service_role;

create or replace function public.reject_research_finalization_artifact_mutation()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  raise integrity_constraint_violation using
    message = 'research finalization artifacts are append-only';
end;
$$;

revoke all on function public.reject_research_finalization_artifact_mutation()
  from public, anon, authenticated, service_role;
create trigger research_finalization_artifacts_append_only
before update or delete on public.research_finalization_artifacts
for each row execute function public.reject_research_finalization_artifact_mutation();

create or replace function public.reserve_deep_research_finalization(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_slots jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_context record;
  v_job public.jobs%rowtype;
  v_slot jsonb;
  v_result jsonb;
  v_results jsonb := '[]'::jsonb;
  v_count integer;
  v_unexpected text[];
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  if v_job.id is null then return null; end if;
  if p_slots is null or jsonb_typeof(p_slots) <> 'array' then
    raise invalid_parameter_value using message = 'finalization slots must be an array';
  end if;
  v_count := jsonb_array_length(p_slots);
  if v_count not between 1 and 50 then
    raise invalid_parameter_value using message = 'finalization slot count is invalid';
  end if;

  for v_slot in select value from jsonb_array_elements(p_slots)
  loop
    select array_agg(key order by key) into v_unexpected
    from jsonb_object_keys(v_slot) key
    where key not in (
      'operationId','idempotencyKey','actionType','costCategory',
      'parametersHash','estimatedCostUsd'
    );
    if jsonb_typeof(v_slot) <> 'object' or v_unexpected is not null
       or v_slot ->> 'costCategory' not in ('r2a','r2e','r2f') then
      raise invalid_parameter_value using message = 'finalization slot is invalid';
    end if;
    v_result := public.reserve_deep_research_operation(
      p_job_id,
      p_worker_id,
      p_lease_generation,
      (v_slot ->> 'operationId')::uuid,
      v_slot ->> 'idempotencyKey',
      v_slot ->> 'actionType',
      v_slot ->> 'costCategory',
      v_slot ->> 'parametersHash',
      (v_slot ->> 'estimatedCostUsd')::numeric
    );
    if v_result is null then return null; end if;
    if v_result ->> 'status' = 'budget_exhausted' then
      raise check_violation using message = 'deep finalization budget is exhausted';
    end if;
    v_results := v_results || jsonb_build_array(v_result || jsonb_build_object(
      'idempotencyKey', v_slot ->> 'idempotencyKey'
    ));
  end loop;
  return jsonb_build_object('operations', v_results);
end;
$$;

create or replace function public.complete_deep_research_final_operation(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_operation_id uuid,
  p_artifact_type text,
  p_artifact_key text,
  p_artifact jsonb,
  p_output_hash text,
  p_provider text,
  p_model_name text,
  p_request_id text,
  p_finish_reason text,
  p_input_tokens integer,
  p_output_tokens integer,
  p_reasoning_tokens integer,
  p_actual_cost_usd numeric,
  p_latency_ms integer,
  p_result_ref text default null
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
  v_existing public.research_finalization_artifacts%rowtype;
  v_artifact_id uuid;
  v_settlement jsonb;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;

  select * into v_operation from public.research_operations
  where id = p_operation_id and research_run_id = v_run.id for update;
  if not found or v_operation.lease_generation <> p_lease_generation then return null; end if;

  select * into v_existing from public.research_finalization_artifacts
  where operation_id = p_operation_id;
  if found then
    if v_operation.status = 'completed'
       and v_existing.artifact_type = p_artifact_type
       and v_existing.artifact_key = p_artifact_key
       and v_existing.artifact_json = p_artifact
       and v_existing.output_hash = p_output_hash
       and v_existing.actual_cost_usd = p_actual_cost_usd then
      return jsonb_build_object(
        'artifactId', v_existing.id, 'operationId', v_operation.id,
        'status', 'completed', 'idempotent', true
      );
    end if;
    raise integrity_constraint_violation using
      message = 'finalization artifact conflicts with durable record';
  end if;

  if v_operation.status <> 'running'
     or (p_artifact_type = 'draft' and v_operation.cost_category <> 'r2a')
     or (p_artifact_type = 'verification' and v_operation.cost_category <> 'r2e')
     or (p_artifact_type in ('repaired_draft','reverification')
         and v_operation.cost_category <> 'r2f')
     or p_artifact_key is null or char_length(p_artifact_key) not between 1 and 200
     or p_artifact is null or jsonb_typeof(p_artifact) <> 'object'
     or p_output_hash !~ '^[0-9a-f]{64}$'
     or p_provider is null or char_length(p_provider) not between 1 and 100
     or p_model_name is null or char_length(p_model_name) not between 1 and 200
     or p_input_tokens is null or p_input_tokens < 0
     or p_output_tokens is null or p_output_tokens < 0
     or p_reasoning_tokens is null or p_reasoning_tokens < 0
     or p_actual_cost_usd is null or p_actual_cost_usd < 0
     or p_latency_ms is null or p_latency_ms < 0 then
    raise invalid_parameter_value using message = 'finalization artifact is invalid';
  end if;

  insert into public.research_finalization_artifacts (
    user_id, research_run_id, operation_id, job_id, lease_generation,
    artifact_type, artifact_key, artifact_json, output_hash,
    provider, model_name, request_id, finish_reason,
    input_tokens, output_tokens, reasoning_tokens, actual_cost_usd, latency_ms
  ) values (
    v_run.user_id, v_run.id, v_operation.id, v_job.id, p_lease_generation,
    p_artifact_type, p_artifact_key, p_artifact, p_output_hash,
    p_provider, p_model_name, nullif(p_request_id,''), nullif(p_finish_reason,''),
    p_input_tokens, p_output_tokens, p_reasoning_tokens, p_actual_cost_usd, p_latency_ms
  ) returning id into v_artifact_id;

  v_settlement := public.settle_deep_research_operation(
    p_job_id, p_worker_id, p_lease_generation, p_operation_id,
    'completed', p_actual_cost_usd, p_result_ref, null
  );
  if v_settlement is null or v_settlement ->> 'status' <> 'completed' then
    raise serialization_failure using message = 'finalization settlement failed';
  end if;
  return jsonb_build_object(
    'artifactId', v_artifact_id, 'operationId', p_operation_id,
    'status', 'completed', 'idempotent', false,
    'actualCostUsd', p_actual_cost_usd
  );
end;
$$;

create or replace function public.load_deep_research_finalization(
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
  v_artifacts jsonb;
  v_operations jsonb;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  select coalesce(jsonb_agg(jsonb_build_object(
    'artifactId', id, 'operationId', operation_id,
    'artifactType', artifact_type, 'artifactKey', artifact_key,
    'artifact', artifact_json, 'outputHash', output_hash,
    'provider', provider, 'modelName', model_name, 'requestId', request_id,
    'finishReason', finish_reason, 'inputTokens', input_tokens,
    'outputTokens', output_tokens, 'reasoningTokens', reasoning_tokens,
    'actualCostUsd', actual_cost_usd, 'latencyMs', latency_ms
  ) order by created_at, id), '[]'::jsonb) into v_artifacts
  from public.research_finalization_artifacts where research_run_id = v_run.id;
  select coalesce(jsonb_agg(jsonb_build_object(
    'operationId', id, 'idempotencyKey', idempotency_key,
    'status', status, 'costCategory', cost_category,
    'estimatedCostUsd', estimated_cost_usd, 'actualCostUsd', actual_cost_usd,
    'uncertainCostUsd', uncertain_cost_usd, 'resultRef', result_ref
  ) order by created_at), '[]'::jsonb) into v_operations
  from public.research_operations
  where research_run_id = v_run.id and cost_category in ('r2a','r2e','r2f');
  return jsonb_build_object('artifacts', v_artifacts, 'operations', v_operations);
end;
$$;

revoke all on function public.reserve_deep_research_finalization(
  uuid,text,bigint,jsonb
) from public, anon, authenticated;
revoke all on function public.complete_deep_research_final_operation(
  uuid,text,bigint,uuid,text,text,jsonb,text,text,text,text,text,
  integer,integer,integer,numeric,integer,text
) from public, anon, authenticated;
revoke all on function public.load_deep_research_finalization(
  uuid,text,bigint
) from public, anon, authenticated;

grant execute on function public.reserve_deep_research_finalization(
  uuid,text,bigint,jsonb
) to service_role;
grant execute on function public.complete_deep_research_final_operation(
  uuid,text,bigint,uuid,text,text,jsonb,text,text,text,text,text,
  integer,integer,integer,numeric,integer,text
) to service_role;
grant execute on function public.load_deep_research_finalization(
  uuid,text,bigint
) to service_role;
