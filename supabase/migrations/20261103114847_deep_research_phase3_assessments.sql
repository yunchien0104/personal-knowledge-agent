create table public.research_assessments (
  id uuid primary key,
  user_id uuid not null,
  research_run_id uuid not null,
  plan_id uuid not null references public.research_plan_versions (id) on delete cascade,
  plan_version integer not null check (plan_version between 1 and 100),
  schema_version text not null check (schema_version = 'evidence-assessment-v1'),
  overall_status text not null check (
    overall_status in ('sufficient', 'partial', 'insufficient', 'conflicted')
  ),
  assessment_json jsonb not null check (
    jsonb_typeof(assessment_json) = 'object'
    and assessment_json ->> 'schemaVersion' = 'evidence-assessment-v1'
    and assessment_json ->> 'assessmentId' = id::text
    and assessment_json ->> 'runId' = research_run_id::text
    and assessment_json ->> 'planId' = plan_id::text
  ),
  model_provider text not null check (char_length(model_provider) between 1 and 64),
  model_name text not null check (char_length(model_name) between 1 and 128),
  prompt_version text not null check (prompt_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
  input_tokens integer not null check (input_tokens >= 0),
  output_tokens integer not null check (output_tokens >= 0),
  latency_ms integer not null check (latency_ms >= 0),
  cost_usd numeric(8,4) not null check (cost_usd >= 0),
  created_at timestamptz not null default now(),
  constraint research_assessments_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint research_assessments_run_plan_unique
    unique (research_run_id, plan_version, id)
);

create table public.research_assessment_gaps (
  assessment_id uuid not null references public.research_assessments (id) on delete cascade,
  user_id uuid not null,
  research_run_id uuid not null,
  gap_id text not null check (gap_id ~ '^[a-z][a-z0-9_-]{1,63}$'),
  requirement_id text not null check (requirement_id ~ '^[a-z][a-z0-9_-]{1,63}$'),
  severity text not null check (severity in ('critical', 'major', 'minor')),
  priority_rank integer not null check (priority_rank between 1 and 40),
  gap_json jsonb not null check (jsonb_typeof(gap_json) = 'object'),
  created_at timestamptz not null default now(),
  primary key (assessment_id, gap_id),
  constraint research_assessment_gaps_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade
);

create index research_assessments_user_run_idx
  on public.research_assessments (user_id, research_run_id, created_at desc);
create index research_assessment_gaps_user_run_rank_idx
  on public.research_assessment_gaps (user_id, research_run_id, priority_rank);

alter table public.research_assessments enable row level security;
alter table public.research_assessments force row level security;
alter table public.research_assessment_gaps enable row level security;
alter table public.research_assessment_gaps force row level security;
create policy research_assessments_select_own on public.research_assessments
for select to authenticated using ((select auth.uid()) = user_id);
create policy research_assessment_gaps_select_own on public.research_assessment_gaps
for select to authenticated using ((select auth.uid()) = user_id);
revoke all on public.research_assessments, public.research_assessment_gaps
  from anon, authenticated;
grant select on public.research_assessments, public.research_assessment_gaps
  to authenticated;
grant select, insert, update, delete on public.research_assessments,
  public.research_assessment_gaps to service_role;

create or replace function public.record_deep_research_assessment(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_assessment jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_plan public.research_plan_versions%rowtype;
  v_assessment_id uuid;
  v_gap jsonb;
  v_rank integer := 0;
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now()
     or v_job.payload_json #>> '{input,researchMode}' <> 'deep' then return null;
  end if;
  select * into v_run from public.research_runs
  where id = (v_job.payload_json #>> '{input,researchRunId}')::uuid
    and user_id = v_job.user_id and job_id = v_job.id and research_mode = 'deep'
  for update;
  if not found or jsonb_typeof(p_assessment) <> 'object'
     or p_assessment ->> 'schemaVersion' <> 'evidence-assessment-v1'
     or p_assessment ->> 'runId' <> v_run.id::text
     or jsonb_typeof(p_assessment -> 'requirementCoverage') <> 'array'
     or jsonb_typeof(p_assessment -> 'prioritizedGaps') <> 'array'
     or jsonb_array_length(p_assessment -> 'prioritizedGaps') > 40 then
    raise invalid_parameter_value using message = 'deep research assessment is invalid';
  end if;
  begin
    v_assessment_id := (p_assessment ->> 'assessmentId')::uuid;
    select * into strict v_plan from public.research_plan_versions
    where id = (p_assessment ->> 'planId')::uuid
      and research_run_id = v_run.id
      and plan_version = (p_assessment ->> 'planVersion')::integer;
  exception when no_data_found or invalid_text_representation or numeric_value_out_of_range then
    raise invalid_parameter_value using message = 'assessment plan identity is invalid';
  end;
  insert into public.research_assessments (
    id, user_id, research_run_id, plan_id, plan_version, schema_version,
    overall_status, assessment_json, model_provider, model_name, prompt_version,
    input_tokens, output_tokens, latency_ms, cost_usd
  ) values (
    v_assessment_id, v_job.user_id, v_run.id, v_plan.id, v_plan.plan_version,
    'evidence-assessment-v1', p_assessment ->> 'overallStatus', p_assessment,
    p_assessment #>> '{telemetry,modelProvider}',
    p_assessment #>> '{telemetry,modelName}',
    p_assessment #>> '{telemetry,promptVersion}',
    (p_assessment #>> '{telemetry,inputTokens}')::integer,
    (p_assessment #>> '{telemetry,outputTokens}')::integer,
    (p_assessment #>> '{telemetry,latencyMs}')::integer,
    (p_assessment #>> '{telemetry,costUsd}')::numeric
  ) on conflict (id) do nothing;
  if not exists (
    select 1 from public.research_assessments
    where id = v_assessment_id and assessment_json = p_assessment
  ) then
    raise integrity_constraint_violation using message = 'assessment identity conflicts';
  end if;
  for v_gap in select value from jsonb_array_elements(p_assessment -> 'prioritizedGaps') loop
    v_rank := v_rank + 1;
    insert into public.research_assessment_gaps (
      assessment_id, user_id, research_run_id, gap_id, requirement_id,
      severity, priority_rank, gap_json
    ) values (
      v_assessment_id, v_job.user_id, v_run.id, v_gap ->> 'id',
      v_gap ->> 'requirementId', v_gap ->> 'severity', v_rank, v_gap
    ) on conflict (assessment_id, gap_id) do nothing;
  end loop;
  update public.research_runs set progress_json = jsonb_build_object(
    'stage', 'deep_assessment_ready', 'planVersion', v_plan.plan_version,
    'assessmentId', v_assessment_id, 'overallStatus', p_assessment ->> 'overallStatus',
    'updatedAt', now()
  ) where id = v_run.id;
  return jsonb_build_object(
    'researchRunId', v_run.id, 'assessmentId', v_assessment_id,
    'overallStatus', p_assessment ->> 'overallStatus', 'gapCount', v_rank
  );
end;
$$;

revoke all on function public.record_deep_research_assessment(uuid,text,bigint,jsonb)
  from public, anon, authenticated, service_role;
grant execute on function public.record_deep_research_assessment(uuid,text,bigint,jsonb)
  to service_role;
