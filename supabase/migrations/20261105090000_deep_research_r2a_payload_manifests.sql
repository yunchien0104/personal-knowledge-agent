-- Append-only, owner-scoped audit records for Deep R2A evidence shaping.
-- Original Evidence remains in asset_evidence/research_run_evidence; this table
-- records only hashes, IDs, byte counts, selected ranges, and coverage impact.

create table public.research_payload_manifests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  research_run_id uuid not null,
  job_id uuid not null references public.jobs (id) on delete cascade,
  lease_generation bigint not null check (lease_generation > 0),
  stage text not null check (stage = 'r2a_synthesis'),
  manifest_json jsonb not null check (
    jsonb_typeof(manifest_json) = 'object'
    and manifest_json ->> 'schemaVersion' = 'r2a-payload-manifest-v1'
    and manifest_json ->> 'algorithmVersion' = 'extractive-coverage-v1'
    and (manifest_json ->> 'manifestHash') ~ '^[0-9a-f]{64}$'
    and jsonb_typeof(manifest_json -> 'includedEvidenceIds') = 'array'
    and jsonb_typeof(manifest_json -> 'excludedEvidence') = 'array'
    and jsonb_typeof(manifest_json -> 'partiallyIncludedEvidence') = 'array'
    and jsonb_typeof(manifest_json -> 'coverageImpact') = 'array'
    and jsonb_typeof(manifest_json -> 'originalInputUpperBound') = 'number'
    and jsonb_typeof(manifest_json -> 'shapedInputUpperBound') = 'number'
    and jsonb_typeof(manifest_json -> 'configuredInputLimit') = 'number'
    and (manifest_json ->> 'originalInputUpperBound')::integer >= 0
    and (manifest_json ->> 'shapedInputUpperBound')::integer >= 0
    and (manifest_json ->> 'configuredInputLimit')::integer >= 256
    and (manifest_json ->> 'shapedInputUpperBound')::integer
      <= (manifest_json ->> 'configuredInputLimit')::integer
    and octet_length(manifest_json::text) <= 1048576
  ),
  manifest_hash text generated always as (manifest_json ->> 'manifestHash') stored,
  created_at timestamptz not null default now(),
  constraint research_payload_manifests_run_owner_fk
    foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint research_payload_manifests_run_stage_unique
    unique (research_run_id, stage)
);

create index research_payload_manifests_user_run_idx
  on public.research_payload_manifests (user_id, research_run_id, created_at);

alter table public.research_payload_manifests enable row level security;
alter table public.research_payload_manifests force row level security;

create policy research_payload_manifests_select_own
on public.research_payload_manifests
for select to authenticated
using ((select auth.uid()) = user_id);

revoke all on public.research_payload_manifests from public, anon, authenticated;
grant select on public.research_payload_manifests to authenticated;
grant select, insert on public.research_payload_manifests to service_role;

create function public.reject_research_payload_manifest_mutation()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  raise integrity_constraint_violation using
    message = 'research payload manifests are append-only';
end;
$$;

revoke all on function public.reject_research_payload_manifest_mutation()
  from public, anon, authenticated, service_role;

create trigger research_payload_manifests_append_only
before update or delete on public.research_payload_manifests
for each row execute function public.reject_research_payload_manifest_mutation();

create function public.record_deep_research_payload_manifest(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_stage text,
  p_manifest jsonb
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
  v_existing public.research_payload_manifests%rowtype;
  v_id uuid;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;

  if p_stage <> 'r2a_synthesis'
     or p_manifest is null
     or jsonb_typeof(p_manifest) <> 'object' then
    raise invalid_parameter_value using message = 'research payload manifest is invalid';
  end if;

  select * into v_existing
  from public.research_payload_manifests
  where research_run_id = v_run.id and stage = p_stage;
  if found then
    if v_existing.manifest_json = p_manifest
       and v_existing.lease_generation <= p_lease_generation then
      return jsonb_build_object(
        'manifestId', v_existing.id,
        'stage', v_existing.stage,
        'manifestHash', v_existing.manifest_hash,
        'idempotent', true
      );
    end if;
    raise integrity_constraint_violation using
      message = 'research payload manifest conflicts with durable record';
  end if;

  insert into public.research_payload_manifests (
    user_id, research_run_id, job_id, lease_generation, stage, manifest_json
  ) values (
    v_run.user_id, v_run.id, v_job.id, p_lease_generation, p_stage, p_manifest
  ) returning id into v_id;

  return jsonb_build_object(
    'manifestId', v_id,
    'stage', p_stage,
    'manifestHash', p_manifest ->> 'manifestHash',
    'idempotent', false
  );
end;
$$;

revoke all on function public.record_deep_research_payload_manifest(
  uuid,text,bigint,text,jsonb
) from public, anon, authenticated;
grant execute on function public.record_deep_research_payload_manifest(
  uuid,text,bigint,text,jsonb
) to service_role;
