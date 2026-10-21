-- Step 4C: atomically publish immutable native-text results behind the active
-- worker lease. Storage upload happens first at a deterministic immutable key;
-- this transaction is the only promotion boundary.

create or replace function public.publish_pdf_ingestion(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_source_hash text,
  p_processor_version text,
  p_object_key text,
  p_artifact_sha256 text,
  p_artifact_size_bytes bigint,
  p_pages jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_asset public.assets%rowtype;
  v_revision public.asset_revisions%rowtype;
  v_page jsonb;
  v_ordinal bigint;
  v_page_number integer;
  v_start_offset integer;
  v_end_offset integer;
  v_previous_end integer := 0;
  v_has_text boolean := false;
  v_expected_key text;
  v_artifact_id uuid;
begin
  if p_worker_id is null or char_length(p_worker_id) not between 1 and 128
     or p_lease_generation is null or p_lease_generation < 1 then
    raise invalid_parameter_value using message = 'worker lease identity is invalid';
  end if;
  if p_source_hash is null or p_source_hash !~ '^[0-9a-f]{64}$'
     or p_artifact_sha256 is null or p_artifact_sha256 !~ '^[0-9a-f]{64}$' then
    raise invalid_parameter_value using message = 'publication hash is invalid';
  end if;
  if p_processor_version is null
     or p_processor_version !~ '^[a-z0-9][a-z0-9._-]{0,63}$' then
    raise invalid_parameter_value using message = 'processor version is invalid';
  end if;
  if p_artifact_size_bytes is null or p_artifact_size_bytes < 1
     or p_pages is null or jsonb_typeof(p_pages) is distinct from 'array' then
    raise invalid_parameter_value using message = 'publication payload is invalid';
  end if;
  if jsonb_array_length(p_pages) not between 1 and 1000 then
    raise invalid_parameter_value using message = 'publication payload is invalid';
  end if;

  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'ingest_pdf' then return null; end if;

  -- A lost response may safely replay the exact completed publication.
  if v_job.status = 'completed' then
    select * into v_revision from public.asset_revisions
    where asset_id = v_job.asset_id
      and fingerprint = p_source_hash
      and processor_version = p_processor_version
      and status = 'validated';
    if found and v_revision.normalized_object_key = p_object_key
       and exists (
         select 1 from public.asset_artifacts
         where revision_id = v_revision.id and user_id = v_job.user_id
           and object_key = p_object_key and sha256 = p_artifact_sha256
           and size_bytes = p_artifact_size_bytes and kind = 'native_pdf_text'
       )
       and (select count(*) from public.asset_evidence
            where revision_id = v_revision.id) = jsonb_array_length(p_pages) then
      return jsonb_build_object(
        'jobId', v_job.id, 'status', 'completed',
        'revisionId', v_revision.id, 'idempotent', true
      );
    end if;
    return null;
  end if;

  if v_job.status <> 'running'
     or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then
    return null;
  end if;

  select * into strict v_asset from public.assets
  where id = v_job.asset_id and user_id = v_job.user_id
  for update;

  v_expected_key := v_job.user_id::text || '/' || v_job.asset_id::text
    || '/revisions/' || p_source_hash || '/' || p_processor_version
    || '/native-text.json';
  if p_object_key is distinct from v_expected_key then
    raise invalid_parameter_value using message = 'artifact object key is invalid';
  end if;

  for v_page, v_ordinal in
    select value, ordinality from jsonb_array_elements(p_pages) with ordinality
  loop
    begin
      if jsonb_typeof(v_page) is distinct from 'object'
         or jsonb_typeof(v_page -> 'page_number') is distinct from 'number'
         or jsonb_typeof(v_page -> 'text') is distinct from 'string'
         or jsonb_typeof(v_page -> 'start_offset') is distinct from 'number'
         or jsonb_typeof(v_page -> 'end_offset') is distinct from 'number'
         or jsonb_typeof(v_page -> 'source_hash') is distinct from 'string'
         or jsonb_typeof(v_page -> 'evidence_locator') is distinct from 'object'
         or (v_page ->> 'page_number') !~ '^[0-9]+$'
         or (v_page ->> 'start_offset') !~ '^[0-9]+$'
         or (v_page ->> 'end_offset') !~ '^[0-9]+$'
         or jsonb_typeof(v_page #> '{evidence_locator,page_number}') is distinct from 'number'
         or jsonb_typeof(v_page #> '{evidence_locator,start_offset}') is distinct from 'number'
         or jsonb_typeof(v_page #> '{evidence_locator,end_offset}') is distinct from 'number'
         or jsonb_typeof(v_page #> '{evidence_locator,kind}') is distinct from 'string'
         or jsonb_typeof(v_page #> '{evidence_locator,source_hash}') is distinct from 'string'
         or (v_page #>> '{evidence_locator,page_number}') !~ '^[0-9]+$'
         or (v_page #>> '{evidence_locator,start_offset}') !~ '^[0-9]+$'
         or (v_page #>> '{evidence_locator,end_offset}') !~ '^[0-9]+$' then
        raise invalid_parameter_value using message = 'page payload is invalid';
      end if;
      v_page_number := (v_page ->> 'page_number')::integer;
      v_start_offset := (v_page ->> 'start_offset')::integer;
      v_end_offset := (v_page ->> 'end_offset')::integer;
    exception when invalid_text_representation or numeric_value_out_of_range then
      raise invalid_parameter_value using message = 'page locator is invalid';
    end;

    if v_page_number <> v_ordinal
       or v_start_offset <> v_previous_end
          + (case when v_ordinal = 1 then 0 else 2 end)
       or v_end_offset <> v_start_offset + char_length(v_page ->> 'text')
       or char_length(v_page ->> 'text') > 1000000
       or v_page ->> 'source_hash' <> p_source_hash
       or v_page #>> '{evidence_locator,kind}' <> 'pdf_page'
       or (v_page #>> '{evidence_locator,page_number}') <> v_page_number::text
       or (v_page #>> '{evidence_locator,start_offset}') <> v_start_offset::text
       or (v_page #>> '{evidence_locator,end_offset}') <> v_end_offset::text
       or (v_page #>> '{evidence_locator,source_hash}') <> p_source_hash then
      raise invalid_parameter_value using message = 'page locator is inconsistent';
    end if;
    v_previous_end := v_end_offset;
    v_has_text := v_has_text or char_length(v_page ->> 'text') > 0;
  end loop;
  if not v_has_text then
    raise invalid_parameter_value using message = 'publication contains no text';
  end if;

  insert into public.asset_revisions (
    user_id, asset_id, revision_no, fingerprint, processor_version,
    status, normalized_object_key
  ) values (
    v_job.user_id, v_job.asset_id,
    coalesce((select max(revision_no) from public.asset_revisions
              where asset_id = v_job.asset_id), 0) + 1,
    p_source_hash, p_processor_version, 'validated', p_object_key
  )
  on conflict (asset_id, fingerprint, processor_version) do nothing;

  select * into strict v_revision from public.asset_revisions
  where asset_id = v_job.asset_id
    and fingerprint = p_source_hash
    and processor_version = p_processor_version;
  if v_revision.status <> 'validated'
     or v_revision.normalized_object_key is distinct from p_object_key then
    raise integrity_constraint_violation using message = 'revision identity conflict';
  end if;

  insert into public.asset_artifacts (
    user_id, revision_id, kind, object_key, sha256, mime, size_bytes, retention_class
  ) values (
    v_job.user_id, v_revision.id, 'native_pdf_text', p_object_key,
    p_artifact_sha256, 'application/json', p_artifact_size_bytes, 'source_derived'
  )
  on conflict (object_key) do nothing
  returning id into v_artifact_id;

  if v_artifact_id is null then
    perform 1 from public.asset_artifacts
    where revision_id = v_revision.id and user_id = v_job.user_id
      and object_key = p_object_key and sha256 = p_artifact_sha256
      and size_bytes = p_artifact_size_bytes and kind = 'native_pdf_text';
    if not found then
      raise integrity_constraint_violation using message = 'artifact identity conflict';
    end if;
  end if;

  if not exists (
    select 1 from public.asset_evidence where revision_id = v_revision.id
  ) then
    insert into public.asset_evidence (
      user_id, revision_id, locator_type, locator_json, excerpt,
      source_hash, content_object_key
    )
    select v_job.user_id, v_revision.id, 'pdf', page -> 'evidence_locator',
           page ->> 'text', p_source_hash, p_object_key
    from jsonb_array_elements(p_pages) as page;
  end if;

  update public.assets
  set active_revision_id = v_revision.id, status = 'ready'
  where id = v_asset.id and user_id = v_asset.user_id;

  update public.job_attempts
  set ended_at = now(), heartbeat_at = now(), outcome = 'completed'
  where job_id = v_job.id and lease_generation = p_lease_generation
    and worker_id = p_worker_id and ended_at is null;

  update public.jobs
  set status = 'completed', lease_owner = null, lease_expires_at = null,
      checkpoint_ref = v_revision.id::text,
      last_error_class = null, last_error_code = null, last_error_message = null
  where id = v_job.id;

  return jsonb_build_object(
    'jobId', v_job.id, 'status', 'completed',
    'revisionId', v_revision.id, 'idempotent', false
  );
end;
$$;

-- Preserve an already-published revision if a later processing attempt fails.
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
    when active_revision_id is not null then 'ready'
    when v_status = 'retry_wait' then 'pending'
    when p_error_class = 'unsupported' then 'unsupported'
    else 'failed'
  end
  where id = v_job.asset_id;
  return v_status;
end;
$$;

revoke execute on function public.publish_pdf_ingestion(
  uuid, text, bigint, text, text, text, text, bigint, jsonb
) from public, anon, authenticated, service_role;
grant execute on function public.publish_pdf_ingestion(
  uuid, text, bigint, text, text, text, text, bigint, jsonb
) to service_role;

revoke execute on function public.report_job_failure(
  uuid, text, bigint, text, text, text, boolean
) from public, anon, authenticated, service_role;
grant execute on function public.report_job_failure(
  uuid, text, bigint, text, text, text, boolean
) to service_role;
