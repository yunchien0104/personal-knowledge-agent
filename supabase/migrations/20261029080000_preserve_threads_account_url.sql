-- Preserve account-qualified Threads URLs. The legacy /t/:id alias can return
-- a generic application shell, so a failed import must be retryable with the
-- original /@account/post/:id path without creating a duplicate Asset.

set local lock_timeout = '5s';
set local statement_timeout = '2min';

create or replace function public.prevent_threads_asset_identity_change()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if old.source_type = 'threads' and (
    new.source_type is distinct from old.source_type
    or new.source_external_id is distinct from old.source_external_id
  ) then
    raise check_violation using message = 'Threads Asset source identity is immutable';
  end if;

  if old.source_type = 'threads' and new.source_url is distinct from old.source_url then
    if old.active_revision_id is not null
       or old.status <> 'failed'
       or not (
         new.source_url = 'https://www.threads.net/t/' || old.source_external_id
         or new.source_url ~ (
           '^https://www[.]threads[.]net/@[^/?#]+/post/'
           || old.source_external_id || '$'
         )
       ) then
      raise check_violation using message = 'Threads Asset source identity is immutable';
    end if;
  end if;
  return new;
end;
$$;

revoke all on function public.prevent_threads_asset_identity_change()
  from public, anon, authenticated, service_role;

create or replace function public.create_threads_asset_and_enqueue(
  p_source_url text,
  p_external_id text,
  p_source_text text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_asset public.assets%rowtype;
  v_job public.jobs%rowtype;
  v_asset_id uuid := gen_random_uuid();
  v_job_id uuid := gen_random_uuid();
  v_key text;
  v_created boolean := false;
  v_requeued boolean := false;
  v_attempt_floor integer;
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  if p_external_id is null or p_external_id !~ '^[A-Za-z0-9_-]{5,64}$'
     or not (
       p_source_url = 'https://www.threads.net/t/' || p_external_id
       or p_source_url ~ (
         '^https://www[.]threads[.]net/@[^/?#]+/post/' || p_external_id || '$'
       )
     ) then
    raise invalid_parameter_value using message = 'canonical Threads post URL is invalid';
  end if;
  if p_source_text is not null then
    p_source_text := btrim(p_source_text);
    if char_length(p_source_text) not between 1 and 100000 then
      raise invalid_parameter_value using message = 'Threads fallback text is invalid';
    end if;
  end if;

  insert into public.assets (
    id, user_id, source_type, source_url, source_external_id
  ) values (
    v_asset_id, v_user_id, 'threads', p_source_url, p_external_id
  )
  on conflict (user_id, source_type, source_external_id)
    where source_external_id is not null
  do nothing
  returning * into v_asset;

  if found then
    v_created := true;
  else
    select * into strict v_asset from public.assets
    where user_id = v_user_id and source_type = 'threads'
      and source_external_id = p_external_id;
  end if;

  v_key := 'ingest_threads:' || v_asset.id::text || ':source-v1';
  if v_created then
    insert into public.jobs (
      id, user_id, asset_id, job_type, payload_json, payload_schema_version,
      idempotency_key, resource_class, priority
    ) values (
      v_job_id, v_user_id, v_asset.id, 'ingest_threads',
      jsonb_build_object(
        'schemaVersion', 1,
        'jobId', v_job_id,
        'userId', v_user_id,
        'jobType', 'ingest_threads',
        'idempotencyKey', v_key,
        'input', jsonb_build_object(
          'assetId', v_asset.id,
          'sourceUrl', v_asset.source_url,
          'sourceExternalId', v_asset.source_external_id,
          'providedText', p_source_text
        )
      ),
      1, v_key, 'media', 100
    );
  end if;

  select * into strict v_job from public.jobs
  where user_id = v_user_id and idempotency_key = v_key;

  if not v_created and v_job.status in ('failed', 'cancelled') then
    select coalesce(max(attempt_no), 0) into v_attempt_floor
    from public.job_attempts where job_id = v_job.id;

    if v_asset.source_url is distinct from p_source_url then
      update public.assets set source_url = p_source_url
      where id = v_asset.id and user_id = v_user_id
      returning * into v_asset;
    end if;

    update public.jobs set
      payload_json = jsonb_set(
        jsonb_set(payload_json, '{input,sourceUrl}', to_jsonb(p_source_url)),
        '{input,providedText}', coalesce(to_jsonb(p_source_text), 'null'::jsonb)
      ),
      status = 'queued',
      attempt_count = greatest(attempt_count, v_attempt_floor),
      max_attempts = greatest(max_attempts, v_attempt_floor + 3),
      next_run_at = now(),
      lease_owner = null,
      lease_expires_at = null,
      last_error_class = null,
      last_error_code = null,
      last_error_message = null,
      cancel_requested_at = null
    where id = v_job.id
    returning * into v_job;

    update public.assets set status = 'pending'
    where id = v_asset.id and user_id = v_user_id
    returning * into v_asset;
    v_requeued := true;
  end if;

  return jsonb_build_object(
    'duplicate', not v_created and not v_requeued,
    'asset', jsonb_build_object(
      'id', v_asset.id,
      'source_type', v_asset.source_type,
      'source_url', v_asset.source_url,
      'source_external_id', v_asset.source_external_id,
      'status', v_asset.status,
      'active_revision_id', v_asset.active_revision_id,
      'created_at', v_asset.created_at,
      'updated_at', v_asset.updated_at
    ),
    'job', jsonb_build_object(
      'id', v_job.id,
      'status', v_job.status,
      'idempotencyKey', v_job.idempotency_key
    )
  );
end;
$$;

revoke all on function public.create_threads_asset_and_enqueue(text,text,text)
  from public, anon, authenticated, service_role;
grant execute on function public.create_threads_asset_and_enqueue(text,text,text)
  to authenticated;
