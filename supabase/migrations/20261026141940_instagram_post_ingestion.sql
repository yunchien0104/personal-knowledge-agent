-- Additive Instagram image-post ingestion. This migration extends the existing
-- source envelope without changing PDF, Reel, or Threads identities. The user
-- creates one canonical /p/ Asset + durable Job; only a fenced Worker may
-- publish immutable normalized text, ordered image artifacts, and Evidence.

set local lock_timeout = '5s';
set local statement_timeout = '2min';

alter table public.assets
  add constraint assets_source_type_check_v2
  check (source_type in ('pdf', 'instagram_reel', 'instagram_post', 'threads'))
  not valid;

alter table public.assets validate constraint assets_source_type_check_v2;
alter table public.assets drop constraint assets_source_type_check;
alter table public.assets
  rename constraint assets_source_type_check_v2 to assets_source_type_check;

create or replace function public.prevent_instagram_post_asset_identity_change()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if old.source_type = 'instagram_post' and (
    new.source_type is distinct from old.source_type
    or new.source_url is distinct from old.source_url
    or new.source_external_id is distinct from old.source_external_id
  ) then
    raise check_violation using message = 'Instagram Post Asset source identity is immutable';
  end if;
  return new;
end;
$$;

revoke all on function public.prevent_instagram_post_asset_identity_change()
  from public, anon, authenticated, service_role;

drop trigger if exists assets_prevent_instagram_post_identity_change on public.assets;
create trigger assets_prevent_instagram_post_identity_change
before update of source_type, source_url, source_external_id on public.assets
for each row execute function public.prevent_instagram_post_asset_identity_change();

create or replace function public.create_instagram_post_asset(
  p_source_url text,
  p_external_id text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_asset public.assets%rowtype;
  v_asset_id uuid := gen_random_uuid();
  v_created boolean := false;
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  if p_external_id is null or p_external_id !~ '^[A-Za-z0-9_-]{5,64}$'
     or p_source_url is distinct from
       'https://www.instagram.com/p/' || p_external_id || '/' then
    raise invalid_parameter_value using message = 'canonical Instagram Post URL is invalid';
  end if;

  insert into public.assets (
    id, user_id, source_type, source_url, source_external_id
  ) values (
    v_asset_id, v_user_id, 'instagram_post', p_source_url, p_external_id
  )
  on conflict (user_id, source_type, source_external_id)
    where source_external_id is not null
  do nothing
  returning * into v_asset;

  if found then
    v_created := true;
  else
    select * into strict v_asset from public.assets
    where user_id = v_user_id and source_type = 'instagram_post'
      and source_external_id = p_external_id;
  end if;

  return jsonb_build_object(
    'duplicate', not v_created,
    'asset', jsonb_build_object(
      'id', v_asset.id,
      'source_type', v_asset.source_type,
      'source_url', v_asset.source_url,
      'source_external_id', v_asset.source_external_id,
      'status', v_asset.status,
      'active_revision_id', v_asset.active_revision_id,
      'created_at', v_asset.created_at,
      'updated_at', v_asset.updated_at
    )
  );
end;
$$;

revoke all on function public.create_instagram_post_asset(text,text)
  from public, anon, authenticated, service_role;
grant execute on function public.create_instagram_post_asset(text,text)
  to authenticated;

create or replace function public.enqueue_instagram_post_ingestion(p_asset_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_asset public.assets%rowtype;
  v_job public.jobs%rowtype;
  v_job_id uuid := gen_random_uuid();
  v_key text;
  v_contract_path text;
  v_media_prefix text;
  v_media_count integer;
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  select * into v_asset from public.assets
  where id = p_asset_id and user_id = v_user_id
    and source_type = 'instagram_post';
  if not found or v_asset.active_revision_id is not null then
    raise check_violation using message = 'pending owned Instagram Post asset required';
  end if;

  v_contract_path := v_user_id::text || '/' || p_asset_id::text
    || '/original/instagram-contract.json';
  v_media_prefix := v_user_id::text || '/' || p_asset_id::text
    || '/original/images';
  perform 1 from storage.objects
  where bucket_id = 'asset-private' and name = v_contract_path
    and owner_id = v_user_id::text;
  if not found then
    raise check_violation using message = 'verified Instagram Contract object not found';
  end if;
  select count(*) into v_media_count from storage.objects
  where bucket_id = 'asset-private'
    and owner_id = v_user_id::text
    and name like v_media_prefix || '/%'
    and (storage.foldername(name))[5] is null
    and coalesce(metadata ->> 'mimetype', '') in ('image/jpeg', 'image/png');
  if v_media_count not between 1 and 20 then
    raise check_violation using message = 'verified Instagram image objects required';
  end if;

  v_key := 'ingest_instagram_post:' || v_asset.id::text || ':contract-v1';
  insert into public.jobs (
    id, user_id, asset_id, job_type, payload_json, payload_schema_version,
    idempotency_key, resource_class, priority
  ) values (
    v_job_id, v_user_id, v_asset.id, 'ingest_instagram_post',
    jsonb_build_object(
      'schemaVersion', 1,
      'jobId', v_job_id,
      'userId', v_user_id,
      'jobType', 'ingest_instagram_post',
      'idempotencyKey', v_key,
      'input', jsonb_build_object(
        'assetId', v_asset.id,
        'sourceUrl', v_asset.source_url,
        'sourceExternalId', v_asset.source_external_id,
        'storageBucket', 'asset-private',
        'contractObjectPath', v_contract_path,
        'mediaPrefix', v_media_prefix
      )
    ),
    1, v_key, 'media', 100
  ) on conflict (user_id, idempotency_key) do nothing;

  select * into strict v_job from public.jobs
  where user_id = v_user_id and idempotency_key = v_key;
  if v_job.status in ('failed', 'cancelled') then
    update public.jobs set
      status = 'queued', attempt_count = 0, next_run_at = now(),
      lease_owner = null, lease_expires_at = null,
      last_error_class = null, last_error_code = null,
      last_error_message = null, cancel_requested_at = null
    where id = v_job.id returning * into v_job;
  end if;
  return jsonb_build_object(
    'id', v_job.id, 'status', v_job.status,
    'idempotencyKey', v_job.idempotency_key
  );
end;
$$;

revoke all on function public.enqueue_instagram_post_ingestion(uuid)
  from public, anon, authenticated, service_role;
grant execute on function public.enqueue_instagram_post_ingestion(uuid)
  to authenticated;

create or replace function public.publish_instagram_post_ingestion(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_source_hash text,
  p_processor_version text,
  p_object_key text,
  p_artifact_sha256 text,
  p_artifact_size_bytes bigint,
  p_media jsonb,
  p_segments jsonb
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
  v_media_item jsonb;
  v_segment jsonb;
  v_locator jsonb;
  v_refs jsonb;
  v_media_id text;
  v_media_object_key text;
  v_segment_id text;
  v_locator_kind text;
  v_start integer;
  v_end integer;
  v_image_index integer;
  v_previous_end integer := 0;
  v_previous_image_index integer := 0;
  v_ordinal bigint;
  v_expected_key text;
  v_artifact_id uuid;
  v_seen_media_ids text[] := array[]::text[];
  v_seen_segment_ids text[] := array[]::text[];
  v_seen_caption boolean := false;
  v_total_media_bytes bigint := 0;
begin
  if p_worker_id is null or char_length(p_worker_id) not between 1 and 128
     or p_lease_generation is null or p_lease_generation < 1 then
    raise invalid_parameter_value using message = 'worker lease identity is invalid';
  end if;
  if p_source_hash is null or p_source_hash !~ '^[0-9a-f]{64}$'
     or p_artifact_sha256 is null or p_artifact_sha256 !~ '^[0-9a-f]{64}$'
     or p_processor_version is null
     or p_processor_version !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
     or p_artifact_size_bytes is null
     or p_artifact_size_bytes not between 1 and 2097152
     or jsonb_typeof(p_media) is distinct from 'array'
     or jsonb_array_length(p_media) not between 1 and 20
     or jsonb_typeof(p_segments) is distinct from 'array'
     or jsonb_array_length(p_segments) not between 1 and 21 then
    raise invalid_parameter_value using message = 'Instagram Post publication payload is invalid';
  end if;

  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'ingest_instagram_post' then return null; end if;

  if v_job.status = 'completed' then
    select * into v_revision from public.asset_revisions
    where asset_id = v_job.asset_id and fingerprint = p_source_hash
      and processor_version = p_processor_version and status = 'validated';
    if found and v_revision.normalized_object_key = p_object_key
       and exists (
         select 1 from public.asset_artifacts
         where revision_id = v_revision.id and user_id = v_job.user_id
           and object_key = p_object_key and sha256 = p_artifact_sha256
           and size_bytes = p_artifact_size_bytes
           and kind = 'instagram_post_normalized'
       )
       and (select count(*) from public.asset_artifacts
            where revision_id = v_revision.id
              and kind = 'instagram_post_image') = jsonb_array_length(p_media)
       and not exists (
         select 1 from jsonb_array_elements(p_media) as media
         where not exists (
           select 1 from public.asset_artifacts
           where revision_id = v_revision.id and user_id = v_job.user_id
             and kind = 'instagram_post_image'
             and object_key = media ->> 'object_key'
             and sha256 = media ->> 'sha256'
             and mime = media ->> 'mime'
             and size_bytes::text = media ->> 'size_bytes'
         )
       )
       and (select count(*) from public.asset_evidence
            where revision_id = v_revision.id) = jsonb_array_length(p_segments)
       and not exists (
         select 1 from jsonb_array_elements(p_segments) as segment
         where not exists (
           select 1 from public.asset_evidence
           where revision_id = v_revision.id and user_id = v_job.user_id
             and locator_json = segment -> 'evidence_locator'
             and excerpt = segment ->> 'text'
             and source_hash = p_source_hash
         )
       ) then
      return jsonb_build_object(
        'jobId', v_job.id,
        'status', 'completed',
        'revisionId', v_revision.id,
        'idempotent', true
      );
    end if;
    return null;
  end if;

  if v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then return null; end if;

  select * into strict v_asset from public.assets
  where id = v_job.asset_id and user_id = v_job.user_id
    and source_type = 'instagram_post'
  for update;

  if v_job.payload_json #>> '{input,sourceUrl}' is distinct from v_asset.source_url
     or v_job.payload_json #>> '{input,sourceExternalId}'
        is distinct from v_asset.source_external_id then
    raise integrity_constraint_violation
      using message = 'Instagram Post Job provenance is inconsistent';
  end if;

  v_expected_key := v_job.user_id::text || '/' || v_job.asset_id::text
    || '/revisions/' || p_source_hash || '/' || p_processor_version
    || '/instagram-post.json';
  if p_object_key is distinct from v_expected_key then
    raise invalid_parameter_value using message = 'artifact object key is invalid';
  end if;

  for v_media_item, v_ordinal in
    select value, ordinality from jsonb_array_elements(p_media) with ordinality
  loop
    if jsonb_typeof(v_media_item) is distinct from 'object'
       or v_media_item - array['id','kind','object_key','sha256','mime','size_bytes']::text[]
          <> '{}'::jsonb
       or not (v_media_item ?& array['id','kind','object_key','sha256','mime','size_bytes'])
       or v_media_item ->> 'kind' <> 'image'
       or (v_media_item ->> 'id') !~ '^[A-Za-z0-9_-]{1,128}$'
       or (v_media_item ->> 'sha256') !~ '^[0-9a-f]{64}$'
       or jsonb_typeof(v_media_item -> 'size_bytes') is distinct from 'number'
       or (v_media_item ->> 'size_bytes') !~ '^[0-9]+$'
       or (v_media_item ->> 'size_bytes')::numeric not between 1 and 10485760
       or v_media_item ->> 'mime' not in ('image/jpeg', 'image/png') then
      raise invalid_parameter_value using message = 'Instagram Post media is invalid';
    end if;
    v_media_id := v_media_item ->> 'id';
    if v_media_id = any(v_seen_media_ids) then
      raise invalid_parameter_value using message = 'Instagram Post media IDs must be unique';
    end if;
    v_media_object_key := v_job.user_id::text || '/' || v_job.asset_id::text
      || '/revisions/' || p_source_hash || '/' || p_processor_version
      || '/images/' || v_media_id;
    if v_media_item ->> 'object_key' is distinct from v_media_object_key then
      raise invalid_parameter_value using message = 'Instagram Post media object key is invalid';
    end if;
    v_total_media_bytes := v_total_media_bytes
      + (v_media_item ->> 'size_bytes')::bigint;
    if v_total_media_bytes > 52428800 then
      raise invalid_parameter_value using message = 'Instagram Post media exceeds the size limit';
    end if;
    v_seen_media_ids := array_append(v_seen_media_ids, v_media_id);
  end loop;

  for v_segment, v_ordinal in
    select value, ordinality from jsonb_array_elements(p_segments) with ordinality
  loop
    if jsonb_typeof(v_segment) is distinct from 'object'
       or v_segment - array['id','text','content_hash','artifact_refs','evidence_locator']::text[]
          <> '{}'::jsonb
       or not (v_segment ?& array['id','text','content_hash','evidence_locator'])
       or jsonb_typeof(v_segment -> 'text') is distinct from 'string'
       or char_length(v_segment ->> 'text') not between 1 and 100000
       or (v_segment ->> 'content_hash') !~ '^[0-9a-f]{64}$'
       or jsonb_typeof(v_segment -> 'evidence_locator') is distinct from 'object' then
      raise invalid_parameter_value using message = 'Instagram Post segment is invalid';
    end if;
    v_segment_id := v_segment ->> 'id';
    if v_segment_id is null or char_length(v_segment_id) not between 1 and 128
       or v_segment_id = any(v_seen_segment_ids) then
      raise invalid_parameter_value using message = 'Instagram Post segment ID is invalid';
    end if;
    v_seen_segment_ids := array_append(v_seen_segment_ids, v_segment_id);
    v_locator := v_segment -> 'evidence_locator';
    v_locator_kind := v_locator ->> 'kind';
    begin
      if (v_locator ->> 'start_offset') !~ '^[0-9]+$'
         or (v_locator ->> 'end_offset') !~ '^[0-9]+$' then
        raise invalid_parameter_value using message = 'Instagram Post locator is invalid';
      end if;
      v_start := (v_locator ->> 'start_offset')::integer;
      v_end := (v_locator ->> 'end_offset')::integer;
    exception when invalid_text_representation or numeric_value_out_of_range then
      raise invalid_parameter_value using message = 'Instagram Post locator is invalid';
    end;
    if v_start <> (case when v_ordinal = 1 then 0 else v_previous_end + 2 end)
       or v_end <> v_start + char_length(v_segment ->> 'text')
       or v_locator ->> 'url' is distinct from v_asset.source_url
       or v_locator ->> 'post_id' is distinct from v_asset.source_external_id
       or v_locator ->> 'source_hash' is distinct from p_source_hash then
      raise invalid_parameter_value using message = 'Instagram Post locator is inconsistent';
    end if;

    if v_locator_kind = 'instagram_post_caption' then
      if v_seen_caption or v_ordinal <> 1
         or v_locator - array['kind','url','post_id','start_offset','end_offset','source_hash']::text[]
            <> '{}'::jsonb
         or (v_segment ? 'artifact_refs' and v_segment -> 'artifact_refs' <> '[]'::jsonb) then
        raise invalid_parameter_value using message = 'Instagram Post caption locator is invalid';
      end if;
      v_seen_caption := true;
    elsif v_locator_kind = 'instagram_image' then
      if v_locator - array[
           'kind','url','post_id','image_index','media_id',
           'start_offset','end_offset','source_hash'
         ]::text[] <> '{}'::jsonb
         or (v_locator ->> 'image_index') !~ '^[0-9]+$'
         or jsonb_typeof(v_segment -> 'artifact_refs') is distinct from 'array' then
        raise invalid_parameter_value using message = 'Instagram image locator is invalid';
      end if;
      begin
        v_image_index := (v_locator ->> 'image_index')::integer;
      exception when invalid_text_representation or numeric_value_out_of_range then
        raise invalid_parameter_value using message = 'Instagram image locator is invalid';
      end;
      v_media_id := v_locator ->> 'media_id';
      v_refs := v_segment -> 'artifact_refs';
      if v_image_index not between 1 and jsonb_array_length(p_media)
         or v_image_index <= v_previous_image_index
         or p_media -> (v_image_index - 1) ->> 'id' is distinct from v_media_id
         or v_refs <> jsonb_build_array(v_media_id) then
        raise invalid_parameter_value using message = 'Instagram image locator is inconsistent';
      end if;
      v_previous_image_index := v_image_index;
    else
      raise invalid_parameter_value using message = 'Instagram Post locator kind is invalid';
    end if;
    v_previous_end := v_end;
  end loop;

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
  where asset_id = v_job.asset_id and fingerprint = p_source_hash
    and processor_version = p_processor_version;
  if v_revision.status <> 'validated'
     or v_revision.normalized_object_key is distinct from p_object_key then
    raise integrity_constraint_violation using message = 'revision identity conflict';
  end if;

  insert into public.asset_artifacts (
    user_id, revision_id, kind, object_key, sha256, mime, size_bytes,
    retention_class
  ) values (
    v_job.user_id, v_revision.id, 'instagram_post_normalized', p_object_key,
    p_artifact_sha256, 'application/json', p_artifact_size_bytes,
    'source_derived'
  )
  on conflict (object_key) do nothing
  returning id into v_artifact_id;

  if v_artifact_id is null then
    perform 1 from public.asset_artifacts
    where revision_id = v_revision.id and user_id = v_job.user_id
      and object_key = p_object_key and sha256 = p_artifact_sha256
      and size_bytes = p_artifact_size_bytes
      and kind = 'instagram_post_normalized';
    if not found then
      raise integrity_constraint_violation using message = 'artifact identity conflict';
    end if;
  end if;

  for v_media_item in select value from jsonb_array_elements(p_media)
  loop
    insert into public.asset_artifacts (
      user_id, revision_id, kind, object_key, sha256, mime, size_bytes,
      retention_class
    ) values (
      v_job.user_id, v_revision.id, 'instagram_post_image',
      v_media_item ->> 'object_key', v_media_item ->> 'sha256',
      v_media_item ->> 'mime', (v_media_item ->> 'size_bytes')::bigint,
      'source_retained'
    )
    on conflict (object_key) do nothing;

    perform 1 from public.asset_artifacts
    where revision_id = v_revision.id and user_id = v_job.user_id
      and object_key = v_media_item ->> 'object_key'
      and sha256 = v_media_item ->> 'sha256'
      and size_bytes = (v_media_item ->> 'size_bytes')::bigint
      and mime = v_media_item ->> 'mime'
      and kind = 'instagram_post_image';
    if not found then
      raise integrity_constraint_violation using message = 'image artifact identity conflict';
    end if;
  end loop;

  if not exists (
    select 1 from public.asset_evidence where revision_id = v_revision.id
  ) then
    insert into public.asset_evidence (
      user_id, revision_id, locator_type, locator_json, excerpt,
      source_hash, content_object_key
    )
    select
      v_job.user_id,
      v_revision.id,
      'post',
      segment -> 'evidence_locator',
      segment ->> 'text',
      p_source_hash,
      case
        when segment #>> '{evidence_locator,kind}' = 'instagram_post_caption'
          then p_object_key
        else (
          select media ->> 'object_key'
          from jsonb_array_elements(p_media) as media
          where media ->> 'id' = segment #>> '{evidence_locator,media_id}'
          limit 1
        )
      end
    from jsonb_array_elements(p_segments) as segment;
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
    'jobId', v_job.id,
    'status', 'completed',
    'revisionId', v_revision.id,
    'idempotent', false
  );
end;
$$;

revoke all on function public.publish_instagram_post_ingestion(
  uuid,text,bigint,text,text,text,text,bigint,jsonb,jsonb
) from public, anon, authenticated, service_role;
grant execute on function public.publish_instagram_post_ingestion(
  uuid,text,bigint,text,text,text,text,bigint,jsonb,jsonb
) to service_role;

-- Instagram-specific adapters feed the existing Source Note Worker without
-- replacing the PDF or Threads enqueue/context RPCs.
create or replace function public.enqueue_instagram_post_source_note_generation(p_asset_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_asset public.assets%rowtype;
  v_revision public.asset_revisions%rowtype;
  v_job public.jobs%rowtype;
  v_job_id uuid := gen_random_uuid();
  v_key text;
  v_attempt_floor integer;
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  select * into v_asset from public.assets
  where id = p_asset_id and user_id = v_user_id
    and source_type = 'instagram_post';
  if not found or v_asset.status <> 'ready' or v_asset.active_revision_id is null then
    raise check_violation using message = 'ready owned Instagram Post asset required';
  end if;
  select * into strict v_revision from public.asset_revisions
  where id = v_asset.active_revision_id and user_id = v_user_id
    and asset_id = v_asset.id and status = 'validated';
  if not exists (
    select 1 from public.asset_artifacts
    where revision_id = v_revision.id and user_id = v_user_id
      and kind = 'instagram_post_normalized'
  ) or not exists (
    select 1 from public.asset_evidence
    where revision_id = v_revision.id and user_id = v_user_id
      and locator_type = 'post'
  ) then
    raise check_violation using message = 'validated source Evidence required';
  end if;

  v_key := 'source_note:' || v_revision.id::text || ':source-note-v2-topics';
  insert into public.jobs (
    id, user_id, asset_id, job_type, payload_json, payload_schema_version,
    idempotency_key, resource_class, priority
  ) values (
    v_job_id, v_user_id, v_asset.id, 'generate_source_note',
    jsonb_build_object(
      'schemaVersion', 1, 'jobId', v_job_id, 'userId', v_user_id,
      'jobType', 'generate_source_note', 'idempotencyKey', v_key,
      'input', jsonb_build_object(
        'assetId', v_asset.id, 'revisionId', v_revision.id,
        'sourceType', 'instagram_post'
      )
    ),
    1, v_key, 'llm', 100
  ) on conflict (user_id, idempotency_key) do nothing;

  select * into strict v_job from public.jobs
  where user_id = v_user_id and idempotency_key = v_key;
  if v_job.status in ('failed', 'cancelled') then
    select coalesce(max(attempt_no), 0) into v_attempt_floor
    from public.job_attempts where job_id = v_job.id;
    update public.jobs set status = 'queued',
      attempt_count = greatest(attempt_count, v_attempt_floor),
      max_attempts = greatest(max_attempts, v_attempt_floor + 3),
      next_run_at = now(), lease_owner = null, lease_expires_at = null,
      last_error_class = null, last_error_code = null,
      last_error_message = null, cancel_requested_at = null
    where id = v_job.id returning * into v_job;
  end if;
  return jsonb_build_object('id', v_job.id, 'status', v_job.status);
end;
$$;

revoke all on function public.enqueue_instagram_post_source_note_generation(uuid)
  from public, anon, authenticated, service_role;
grant execute on function public.enqueue_instagram_post_source_note_generation(uuid)
  to authenticated;

create or replace function public.get_instagram_post_source_note_context(
  p_job_id uuid, p_worker_id text, p_lease_generation bigint
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
  v_object_key text;
begin
  select * into v_job from public.jobs where id = p_job_id;
  if not found or v_job.job_type <> 'generate_source_note'
     or v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then return null; end if;
  select * into v_asset from public.assets
  where id = v_job.asset_id and user_id = v_job.user_id
    and source_type = 'instagram_post';
  if not found then return null; end if;
  select * into v_revision from public.asset_revisions
  where id = (v_job.payload_json #>> '{input,revisionId}')::uuid
    and id = v_asset.active_revision_id and asset_id = v_asset.id
    and user_id = v_job.user_id and status = 'validated';
  if not found then return null; end if;
  select object_key into v_object_key from public.asset_artifacts
  where revision_id = v_revision.id and user_id = v_job.user_id
    and kind = 'instagram_post_normalized'
  order by created_at desc limit 1;
  if v_object_key is null then return null; end if;
  return jsonb_build_object(
    'assetId', v_job.asset_id,
    'revisionId', v_revision.id,
    'sourceType', v_asset.source_type,
    'sourceUrl', v_asset.source_url,
    'sourceFingerprint', v_revision.fingerprint,
    'artifactObjectKey', v_object_key,
    'evidence', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', id, 'excerpt', excerpt, 'locator', locator_json,
        'sourceHash', source_hash
      ) order by case
        when locator_type = 'post' then (locator_json ->> 'start_offset')::integer
        else null
      end, id)
      from public.asset_evidence
      where revision_id = v_revision.id and user_id = v_job.user_id
        and locator_type = 'post'
    ), '[]'::jsonb)
  );
end;
$$;

revoke all on function public.get_instagram_post_source_note_context(uuid,text,bigint)
  from public, anon, authenticated, service_role;
grant execute on function public.get_instagram_post_source_note_context(uuid,text,bigint)
  to service_role;

-- Instagram-specific retrieval queue/context RPCs keep the existing PDF and
-- future Threads functions unchanged. Publication still uses the shared,
-- fenced publish_retrieval_index RPC.
create or replace function public.enqueue_instagram_post_retrieval_index_jobs(
  p_embedding_model text,
  p_embedding_version text,
  p_limit integer default 25
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_count integer;
begin
  if p_embedding_model is null or char_length(p_embedding_model) not between 1 and 128
     or p_embedding_version is null
     or p_embedding_version !~ '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$'
     or p_limit not between 1 and 100 then
    raise invalid_parameter_value using message = 'retrieval enqueue request is invalid';
  end if;
  with candidates as (
    select a.user_id, a.id asset_id, r.id revision_id
    from public.assets a
    join public.asset_revisions r
      on r.id = a.active_revision_id and r.user_id = a.user_id
      and r.asset_id = a.id and r.status = 'validated'
    where a.source_type = 'instagram_post' and a.status = 'ready'
      and r.normalized_object_key is not null
      and not exists (
        select 1 from public.retrieval_chunks c
        where c.user_id = a.user_id and c.revision_id = r.id
          and c.embedding_model = p_embedding_model
          and c.embedding_version = p_embedding_version
          and c.embedding_status = 'ready'
      )
    order by r.created_at, r.id
    limit p_limit
  ), inserted as (
    insert into public.jobs (
      id, user_id, asset_id, job_type, payload_json, payload_schema_version,
      idempotency_key, resource_class, priority, max_attempts
    )
    select job.id, c.user_id, c.asset_id, 'index_retrieval',
      jsonb_build_object(
        'schemaVersion', 1, 'jobId', job.id, 'userId', c.user_id,
        'jobType', 'index_retrieval', 'idempotencyKey', job.key,
        'input', jsonb_build_object(
          'assetId', c.asset_id, 'revisionId', c.revision_id,
          'sourceType', 'instagram_post',
          'embeddingModel', p_embedding_model, 'embeddingVersion', p_embedding_version
        )
      ), 1, job.key, 'retrieval', 90, 5
    from candidates c
    cross join lateral (
      select gen_random_uuid() id,
        'retrieval-index:' || c.revision_id::text || ':'
          || md5(p_embedding_model || ':' || p_embedding_version) key
    ) job
    on conflict (user_id, idempotency_key) do nothing
    returning 1
  )
  select count(*) into v_count from inserted;
  return v_count;
end;
$$;

create or replace function public.get_instagram_post_retrieval_index_context(
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
  v_job public.jobs%rowtype;
  v_revision public.asset_revisions%rowtype;
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'index_retrieval'
     or v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then return null; end if;
  select r.* into v_revision
  from public.asset_revisions r
  join public.assets a
    on a.id = r.asset_id and a.user_id = r.user_id
    and a.active_revision_id = r.id and a.status = 'ready'
    and a.source_type = 'instagram_post'
  where r.id = (v_job.payload_json #>> '{input,revisionId}')::uuid
    and r.user_id = v_job.user_id and r.asset_id = v_job.asset_id
    and r.status = 'validated';
  if not found then
    raise integrity_constraint_violation using message = 'retrieval source revision is no longer active';
  end if;
  return jsonb_build_object(
    'userId', v_job.user_id, 'assetId', v_job.asset_id,
    'revisionId', v_revision.id, 'sourceType', 'instagram_post',
    'sourceFingerprint', v_revision.fingerprint,
    'artifactObjectKey', v_revision.normalized_object_key,
    'evidence', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'id', e.id, 'sourceHash', e.source_hash,
        'locator', e.locator_json, 'excerpt', e.excerpt
      ) order by case
        when e.locator_type = 'pdf' then (e.locator_json ->> 'page_number')::integer
        else (e.locator_json ->> 'start_offset')::integer
      end, e.id), '[]'::jsonb)
      from public.asset_evidence e
      where e.revision_id = v_revision.id and e.user_id = v_job.user_id
    )
  );
end;
$$;

revoke all on function public.enqueue_instagram_post_retrieval_index_jobs(text,text,integer)
  from public, anon, authenticated, service_role;
revoke all on function public.get_instagram_post_retrieval_index_context(uuid,text,bigint)
  from public, anon, authenticated, service_role;
grant execute on function public.enqueue_instagram_post_retrieval_index_jobs(text,text,integer)
  to service_role;
grant execute on function public.get_instagram_post_retrieval_index_context(uuid,text,bigint)
  to service_role;
