-- Add monotonic, lease-fenced progress reporting for durable jobs. Progress is
-- stored on the existing RLS-protected jobs row; only service_role may mutate
-- it through the Worker RPC.

alter table public.jobs
  add column progress_percent smallint not null default 0
    constraint jobs_progress_percent_check check (progress_percent between 0 and 100),
  add column progress_stage text not null default 'queued'
    constraint jobs_progress_stage_check check (
      progress_stage ~ '^[a-z][a-z0-9_]{1,63}$'
    ),
  add column progress_updated_at timestamptz not null default now();

update public.jobs
set progress_percent = case when status = 'completed' then 100 else 0 end,
    progress_stage = case
      when status = 'completed' then 'completed'
      when status = 'failed' then 'failed'
      when status = 'cancelled' then 'cancelled'
      when status = 'running' then 'starting'
      when status = 'retry_wait' then 'retry_wait'
      else 'queued'
    end,
    progress_updated_at = now();

create or replace function public.sync_job_progress_from_status()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if new.status = 'completed' then
    new.progress_percent := 100;
    new.progress_stage := 'completed';
    new.progress_updated_at := now();
  elsif new.status = 'failed' then
    new.progress_stage := 'failed';
    new.progress_updated_at := now();
  elsif new.status = 'cancelled' then
    new.progress_stage := 'cancelled';
    new.progress_updated_at := now();
  elsif new.status = 'retry_wait' then
    new.progress_stage := 'retry_wait';
    new.progress_updated_at := now();
  elsif new.status = 'running' and old.status is distinct from 'running' then
    new.progress_percent := greatest(new.progress_percent, 1);
    new.progress_stage := 'starting';
    new.progress_updated_at := now();
  end if;
  return new;
end;
$$;

create trigger jobs_sync_progress_from_status
before update of status on public.jobs
for each row execute function public.sync_job_progress_from_status();

create or replace function public.report_job_progress(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_progress_percent integer,
  p_progress_stage text
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_updated boolean;
begin
  if p_progress_percent not between 1 and 99 then
    raise invalid_parameter_value using message = 'job progress percentage is invalid';
  end if;
  if p_progress_stage is null
     or p_progress_stage !~ '^[a-z][a-z0-9_]{1,63}$' then
    raise invalid_parameter_value using message = 'job progress stage is invalid';
  end if;

  update public.jobs
  set progress_percent = p_progress_percent,
      progress_stage = p_progress_stage,
      progress_updated_at = now()
  where id = p_job_id
    and status = 'running'
    and lease_owner = p_worker_id
    and lease_generation = p_lease_generation
    and lease_expires_at > now()
    and progress_percent <= p_progress_percent
  returning true into v_updated;

  return coalesce(v_updated, false);
end;
$$;

revoke all on function public.sync_job_progress_from_status() from public, anon, authenticated;
revoke all on function public.report_job_progress(uuid,text,bigint,integer,text) from public, anon, authenticated;
grant execute on function public.report_job_progress(uuid,text,bigint,integer,text) to service_role;
