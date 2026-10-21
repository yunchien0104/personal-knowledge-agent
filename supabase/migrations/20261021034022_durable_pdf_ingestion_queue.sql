-- Step 4A: durable PostgreSQL queue for native-text PDF ingestion.
-- User-facing enqueue is ownership checked. Worker lease functions are callable
-- only by service_role and every mutation is fenced by owner + generation.

create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  asset_id uuid not null,
  job_type text not null
    constraint jobs_job_type_check check (job_type ~ '^[a-z][a-z0-9_]{1,63}$'),
  payload_json jsonb not null
    constraint jobs_payload_object_check check (jsonb_typeof(payload_json) = 'object'),
  payload_schema_version integer not null default 1
    constraint jobs_payload_schema_version_check check (payload_schema_version = 1),
  status text not null default 'queued'
    constraint jobs_status_check check (
      status in (
        'queued', 'running', 'retry_wait', 'paused_budget', 'paused_user',
        'completed', 'failed', 'cancelled'
      )
    ),
  priority smallint not null default 100
    constraint jobs_priority_check check (priority between 0 and 1000),
  resource_class text not null default 'lightweight'
    constraint jobs_resource_class_check check (resource_class in ('lightweight', 'media')),
  idempotency_key text not null
    constraint jobs_idempotency_key_check check (char_length(idempotency_key) between 1 and 128),
  attempt_count integer not null default 0
    constraint jobs_attempt_count_check check (attempt_count >= 0),
  max_attempts integer not null default 3
    constraint jobs_max_attempts_check check (max_attempts between 1 and 10),
  next_run_at timestamptz not null default now(),
  lease_owner text,
  lease_expires_at timestamptz,
  lease_generation bigint not null default 0
    constraint jobs_lease_generation_check check (lease_generation >= 0),
  checkpoint_ref text,
  cancel_requested_at timestamptz,
  last_error_class text
    constraint jobs_error_class_check check (
      last_error_class is null
      or last_error_class in ('transient', 'permanent', 'unsupported')
    ),
  last_error_code text,
  last_error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint jobs_id_user_id_unique unique (id, user_id),
  constraint jobs_user_idempotency_unique unique (user_id, idempotency_key),
  constraint jobs_asset_owner_fk
    foreign key (asset_id, user_id)
    references public.assets (id, user_id)
    on delete cascade,
  constraint jobs_payload_identity_check check (
    payload_json ?& array[
      'schemaVersion', 'jobId', 'userId', 'jobType', 'idempotencyKey', 'input'
    ]
    and payload_json ->> 'schemaVersion' = payload_schema_version::text
    and jsonb_typeof(payload_json -> 'input') = 'object'
    and (payload_json -> 'input') ? 'assetId'
    and payload_json ->> 'jobId' = id::text
    and payload_json ->> 'userId' = user_id::text
    and payload_json ->> 'jobType' = job_type
    and payload_json ->> 'idempotencyKey' = idempotency_key
    and payload_json #>> '{input,assetId}' = asset_id::text
  ),
  constraint jobs_lease_state_check check (
    (status = 'running' and lease_owner is not null and lease_expires_at is not null)
    or (status <> 'running' and lease_owner is null and lease_expires_at is null)
  )
);

create index jobs_user_status_created_idx
  on public.jobs (user_id, status, created_at desc);
create index jobs_asset_created_idx
  on public.jobs (asset_id, created_at desc);
create index jobs_available_claim_idx
  on public.jobs (priority desc, next_run_at, created_at)
  where status in ('queued', 'retry_wait');
create index jobs_expired_lease_idx
  on public.jobs (lease_expires_at)
  where status = 'running';

create table public.job_attempts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  job_id uuid not null,
  attempt_no integer not null constraint job_attempts_attempt_no_check check (attempt_no > 0),
  lease_generation bigint not null
    constraint job_attempts_lease_generation_check check (lease_generation > 0),
  worker_id text not null
    constraint job_attempts_worker_id_check check (char_length(worker_id) between 1 and 128),
  started_at timestamptz not null default now(),
  heartbeat_at timestamptz not null default now(),
  ended_at timestamptz,
  outcome text
    constraint job_attempts_outcome_check check (
      outcome is null or outcome in ('completed', 'retry', 'failed', 'lease_expired', 'cancelled')
    ),
  error_class text
    constraint job_attempts_error_class_check check (
      error_class is null or error_class in ('transient', 'permanent', 'unsupported')
    ),
  error_code text,
  error_message text,
  constraint job_attempts_job_owner_fk
    foreign key (job_id, user_id)
    references public.jobs (id, user_id)
    on delete cascade,
  constraint job_attempts_job_attempt_unique unique (job_id, attempt_no),
  constraint job_attempts_job_generation_unique unique (job_id, lease_generation)
);

create index job_attempts_user_job_started_idx
  on public.job_attempts (user_id, job_id, started_at desc);

create trigger jobs_set_updated_at
before update on public.jobs
for each row execute function public.set_current_timestamp_updated_at();

alter table public.jobs enable row level security;
alter table public.jobs force row level security;
alter table public.job_attempts enable row level security;
alter table public.job_attempts force row level security;

create policy jobs_select_own on public.jobs
for select to authenticated
using ((select auth.uid()) = user_id);
create policy job_attempts_select_own on public.job_attempts
for select to authenticated
using ((select auth.uid()) = user_id);

revoke all on public.jobs from anon, authenticated;
revoke all on public.job_attempts from anon, authenticated;
grant select on public.jobs, public.job_attempts to authenticated;
grant select, insert, update, delete on public.jobs, public.job_attempts to service_role;

create or replace function public.enqueue_pdf_ingestion(p_asset_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_job_id uuid := gen_random_uuid();
  v_idempotency_key text := 'ingest_pdf:' || p_asset_id::text || ':original-v1';
  v_object_path text;
  v_job public.jobs%rowtype;
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;

  perform 1 from public.assets
  where id = p_asset_id and user_id = v_user_id and source_type = 'pdf';
  if not found then
    raise insufficient_privilege using message = 'owned PDF asset not found';
  end if;

  v_object_path := v_user_id::text || '/' || p_asset_id::text || '/original/source.pdf';
  perform 1 from storage.objects
  where bucket_id = 'asset-private' and name = v_object_path;
  if not found then
    raise check_violation using message = 'verified PDF object not found';
  end if;

  insert into public.jobs (
    id, user_id, asset_id, job_type, payload_json,
    payload_schema_version, idempotency_key
  ) values (
    v_job_id,
    v_user_id,
    p_asset_id,
    'ingest_pdf',
    jsonb_build_object(
      'schemaVersion', 1,
      'jobId', v_job_id,
      'userId', v_user_id,
      'jobType', 'ingest_pdf',
      'idempotencyKey', v_idempotency_key,
      'input', jsonb_build_object(
        'assetId', p_asset_id,
        'storageBucket', 'asset-private',
        'objectPath', v_object_path
      )
    ),
    1,
    v_idempotency_key
  ) on conflict (user_id, idempotency_key) do nothing;

  select * into strict v_job from public.jobs
  where user_id = v_user_id and idempotency_key = v_idempotency_key;

  return jsonb_build_object(
    'id', v_job.id,
    'status', v_job.status,
    'idempotencyKey', v_job.idempotency_key
  );
end;
$$;

create or replace function public.claim_job(
  p_worker_id text,
  p_resource_class text default null,
  p_lease_seconds integer default 120
)
returns setof public.jobs
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
begin
  if p_worker_id is null or char_length(p_worker_id) not between 1 and 128 then
    raise invalid_parameter_value using message = 'worker id is invalid';
  end if;
  if p_resource_class is not null and p_resource_class not in ('lightweight', 'media') then
    raise invalid_parameter_value using message = 'resource class is invalid';
  end if;
  if p_lease_seconds not between 30 and 600 then
    raise invalid_parameter_value using message = 'lease duration is invalid';
  end if;

  loop
    select * into v_job from public.jobs
    where cancel_requested_at is null
      and (p_resource_class is null or resource_class = p_resource_class)
      and (
        (status in ('queued', 'retry_wait') and next_run_at <= now())
        or (status = 'running' and lease_expires_at <= now())
      )
    order by (
      priority
      + least(
          100,
          greatest(0, floor(extract(epoch from (now() - created_at)) / 3600)::integer)
        )
    ) desc, created_at, id
    limit 1
    for update skip locked;

    if not found then return; end if;

    -- Preserve the explicit retry_wait -> queued -> running state sequence while
    -- retaining the row lock for a single atomic claim transaction.
    if v_job.status = 'retry_wait' then
      update public.jobs set status = 'queued' where id = v_job.id;
      v_job.status := 'queued';
    end if;

    if v_job.attempt_count >= v_job.max_attempts then
      update public.jobs
      set status = 'failed', lease_owner = null, lease_expires_at = null,
          last_error_class = 'permanent', last_error_code = 'attempts_exhausted',
          last_error_message = 'Maximum processing attempts were exhausted.'
      where id = v_job.id;
      update public.job_attempts
      set ended_at = coalesce(ended_at, now()),
          outcome = coalesce(outcome, 'lease_expired'),
          error_class = coalesce(error_class, 'transient'),
          error_code = coalesce(error_code, 'lease_expired')
      where job_id = v_job.id and ended_at is null;
      update public.assets set status = 'failed' where id = v_job.asset_id;
      continue;
    end if;

    if v_job.status = 'running' then
      update public.job_attempts
      set ended_at = now(), outcome = 'lease_expired', error_class = 'transient',
          error_code = 'lease_expired',
          error_message = 'Worker lease expired before completion.'
      where job_id = v_job.id
        and lease_generation = v_job.lease_generation
        and ended_at is null;
    end if;

    update public.jobs
    set status = 'running', attempt_count = attempt_count + 1,
        lease_owner = p_worker_id,
        lease_expires_at = now() + make_interval(secs => p_lease_seconds),
        lease_generation = lease_generation + 1
    where id = v_job.id
    returning * into v_job;

    insert into public.job_attempts (
      user_id, job_id, attempt_no, lease_generation, worker_id
    ) values (
      v_job.user_id, v_job.id, v_job.attempt_count, v_job.lease_generation, p_worker_id
    );
    update public.assets set status = 'processing' where id = v_job.asset_id;
    return next v_job;
    return;
  end loop;
end;
$$;

create or replace function public.heartbeat_job(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_lease_seconds integer default 120
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_updated boolean;
begin
  if p_lease_seconds not between 30 and 600 then
    raise invalid_parameter_value using message = 'lease duration is invalid';
  end if;

  update public.jobs
  set lease_expires_at = now() + make_interval(secs => p_lease_seconds)
  where id = p_job_id and status = 'running'
    and lease_owner = p_worker_id and lease_generation = p_lease_generation
    and lease_expires_at > now()
  returning true into v_updated;

  if coalesce(v_updated, false) then
    update public.job_attempts set heartbeat_at = now()
    where job_id = p_job_id and lease_generation = p_lease_generation
      and worker_id = p_worker_id and ended_at is null;
  end if;
  return coalesce(v_updated, false);
end;
$$;

create or replace function public.report_job_failure(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_error_class text,
  p_error_code text,
  p_error_message text,
  p_retryable boolean
)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_status text;
  v_backoff_seconds integer;
begin
  if p_error_class is null
     or p_error_class not in ('transient', 'permanent', 'unsupported') then
    raise invalid_parameter_value using message = 'error class is invalid';
  end if;
  if p_retryable and p_error_class <> 'transient' then
    raise invalid_parameter_value using message = 'only transient errors may be retried';
  end if;
  if p_error_code is null or char_length(p_error_code) not between 1 and 128
     or p_error_message is null or char_length(p_error_message) not between 1 and 500
     or p_retryable is null then
    raise invalid_parameter_value using message = 'error detail is invalid';
  end if;

  select * into v_job from public.jobs
  where id = p_job_id and status = 'running'
    and lease_owner = p_worker_id and lease_generation = p_lease_generation
    and lease_expires_at > now()
  for update;
  if not found then return null; end if;

  if p_retryable and v_job.attempt_count < v_job.max_attempts then
    v_status := 'retry_wait';
    v_backoff_seconds := least(
      3600,
      (5 * power(2, greatest(v_job.attempt_count - 1, 0)))::integer
        + floor(random() * 5)::integer
    );
  else
    v_status := 'failed';
    v_backoff_seconds := 0;
  end if;

  update public.jobs
  set status = v_status,
      next_run_at = case
        when v_status = 'retry_wait' then now() + make_interval(secs => v_backoff_seconds)
        else next_run_at
      end,
      lease_owner = null, lease_expires_at = null,
      last_error_class = p_error_class, last_error_code = p_error_code,
      last_error_message = p_error_message
  where id = p_job_id;

  update public.job_attempts
  set ended_at = now(),
      outcome = case when v_status = 'retry_wait' then 'retry' else 'failed' end,
      error_class = p_error_class, error_code = p_error_code,
      error_message = p_error_message
  where job_id = p_job_id and lease_generation = p_lease_generation
    and worker_id = p_worker_id and ended_at is null;

  update public.assets
  set status = case
    when v_status = 'retry_wait' then 'pending'
    when p_error_class = 'unsupported' then 'unsupported'
    else 'failed'
  end
  where id = v_job.asset_id;
  return v_status;
end;
$$;

revoke execute on function public.enqueue_pdf_ingestion(uuid)
  from public, anon, authenticated, service_role;
revoke execute on function public.claim_job(text, text, integer)
  from public, anon, authenticated, service_role;
revoke execute on function public.heartbeat_job(uuid, text, bigint, integer)
  from public, anon, authenticated, service_role;
revoke execute on function public.report_job_failure(uuid, text, bigint, text, text, text, boolean)
  from public, anon, authenticated, service_role;

grant execute on function public.enqueue_pdf_ingestion(uuid) to authenticated;
grant execute on function public.claim_job(text, text, integer) to service_role;
grant execute on function public.heartbeat_job(uuid, text, bigint, integer) to service_role;
grant execute on function public.report_job_failure(uuid, text, bigint, text, text, text, boolean)
  to service_role;
