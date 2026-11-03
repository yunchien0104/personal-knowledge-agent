-- Deep Research Phase 1 is additive. Legacy start_research(), its plan JSON,
-- and every existing publication function remain unchanged.

alter table public.research_runs
  add column request_id uuid,
  add column research_mode text not null default 'legacy',
  add column web_policy text not null default 'disabled',
  add column request_json jsonb;

update public.research_runs set request_id = id where request_id is null;

alter table public.research_runs
  alter column request_id set not null,
  alter column request_id set default gen_random_uuid(),
  add constraint research_runs_request_id_unique unique (request_id),
  add constraint research_runs_mode_check
    check (research_mode in ('legacy', 'deep')),
  add constraint research_runs_web_policy_check
    check (web_policy in ('disabled', 'suggest', 'auto')),
  add constraint research_runs_request_contract_check check (
    (research_mode = 'legacy' and request_json is null)
    or (
      research_mode = 'deep'
      and jsonb_typeof(request_json) = 'object'
      and request_json ->> 'schemaVersion' = 'research-request-v1'
      and request_json ->> 'executionMode' = 'deep'
      and request_json ->> 'runId' = id::text
      and request_json ->> 'requestId' = request_id::text
      and request_json ->> 'userId' = user_id::text
    )
  );

create table public.deep_research_account_settings (
  user_id uuid primary key references auth.users (id) on delete cascade,
  web_auto_authorized boolean not null default false,
  max_rounds integer not null default 3 check (max_rounds between 1 and 10),
  max_queries integer not null default 12 check (max_queries between 1 and 50),
  max_cost_usd numeric(8,4) not null default 0.5000
    check (max_cost_usd between 0 and 0.5000),
  max_duration_seconds integer not null default 1800
    check (max_duration_seconds between 30 and 14400),
  updated_at timestamptz not null default now()
);

alter table public.deep_research_account_settings enable row level security;
alter table public.deep_research_account_settings force row level security;
create policy deep_research_account_settings_select_own
on public.deep_research_account_settings for select to authenticated
using ((select auth.uid()) = user_id);
revoke all on public.deep_research_account_settings from anon, authenticated;
grant select on public.deep_research_account_settings to authenticated;
grant select, insert, update, delete on public.deep_research_account_settings to service_role;

create table public.research_plan_versions (
  id uuid primary key,
  user_id uuid not null,
  research_run_id uuid not null,
  request_id uuid not null,
  plan_version integer not null check (plan_version between 1 and 100),
  schema_version text not null check (schema_version = 'research-plan-v1'),
  plan_json jsonb not null check (
    jsonb_typeof(plan_json) = 'object'
    and plan_json ->> 'schemaVersion' = 'research-plan-v1'
    and plan_json ->> 'planId' = id::text
    and plan_json ->> 'runId' = research_run_id::text
    and plan_json ->> 'requestId' = request_id::text
    and (plan_json ->> 'planVersion')::integer = plan_version
  ),
  parent_plan_id uuid references public.research_plan_versions (id) on delete restrict,
  revision_reason text check (
    revision_reason is null or char_length(revision_reason) between 1 and 1000
  ),
  quality_gate text not null check (
    quality_gate in ('hard_fail', 'needs_revision', 'acceptable')
  ),
  model_provider text check (
    model_provider is null or char_length(model_provider) between 1 and 64
  ),
  model_name text check (
    model_name is null or char_length(model_name) between 1 and 128
  ),
  prompt_version text check (
    prompt_version is null or prompt_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'
  ),
  created_at timestamptz not null default now(),
  constraint research_plan_versions_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint research_plan_versions_request_fk
    foreign key (request_id) references public.research_runs (request_id) on delete cascade,
  constraint research_plan_versions_run_version_unique
    unique (research_run_id, plan_version),
  constraint research_plan_versions_revision_pair_check check (
    (parent_plan_id is null) = (revision_reason is null)
  )
);

create index research_plan_versions_user_run_idx
  on public.research_plan_versions (user_id, research_run_id, plan_version desc);

alter table public.research_plan_versions enable row level security;
alter table public.research_plan_versions force row level security;
create policy research_plan_versions_select_own
on public.research_plan_versions for select to authenticated
using ((select auth.uid()) = user_id);
revoke all on public.research_plan_versions from anon, authenticated;
grant select on public.research_plan_versions to authenticated;
grant select, insert, update, delete on public.research_plan_versions to service_role;

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
    v_settings.max_cost_usd := 0.5000;
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
    'maxCostUsd', least(v_requested_cost, v_settings.max_cost_usd, 0.5000),
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
