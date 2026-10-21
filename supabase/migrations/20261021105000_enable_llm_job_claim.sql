-- Step 5A follow-up: the already-deployed Queue claimant predates the `llm`
-- resource class. Preserve its lease/fencing behavior while allowing Source Note
-- Jobs and keeping the successfully ingested Asset ready during note generation.

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
  if p_resource_class is not null
     and p_resource_class not in ('lightweight', 'media', 'llm') then
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
      if v_job.job_type = 'ingest_pdf' then
        update public.assets set status = 'failed' where id = v_job.asset_id;
      end if;
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
    if v_job.job_type = 'ingest_pdf' then
      update public.assets set status = 'processing' where id = v_job.asset_id;
    end if;
    return next v_job;
    return;
  end loop;
end;
$$;

revoke execute on function public.claim_job(text,text,integer)
  from public, anon, authenticated, service_role;
grant execute on function public.claim_job(text,text,integer) to service_role;
