-- URL-only Instagram Reel ingestion. This migration is additive: the existing
-- Contract-upload bridge remains available for rollback and offline imports.
set local lock_timeout = '5s';
set local statement_timeout = '2min';

create or replace function public.enqueue_instagram_reel_cloud_ingestion(p_asset_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user uuid := auth.uid();
  v_asset public.assets%rowtype;
  v_job public.jobs%rowtype;
  v_id uuid := gen_random_uuid();
  v_key text;
  v_floor integer;
begin
  if v_user is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;

  select * into v_asset
  from public.assets
  where id = p_asset_id and user_id = v_user and source_type = 'instagram_reel';

  if not found or v_asset.active_revision_id is not null then
    raise check_violation using message = 'pending owned Instagram Reel asset required';
  end if;

  v_key := 'ingest_instagram_reel:' || v_asset.id || ':cloud-v2';
  insert into public.jobs(
    id,user_id,asset_id,job_type,payload_json,payload_schema_version,
    idempotency_key,resource_class,priority
  ) values (
    v_id,v_user,v_asset.id,'ingest_instagram_reel',jsonb_build_object(
      'schemaVersion',1,
      'jobId',v_id,
      'userId',v_user,
      'jobType','ingest_instagram_reel',
      'idempotencyKey',v_key,
      'input',jsonb_build_object(
        'assetId',v_asset.id,
        'sourceUrl',v_asset.source_url,
        'sourceExternalId',v_asset.source_external_id,
        'acquisitionMode','cloud'
      )
    ),1,v_key,'media',100
  ) on conflict(user_id,idempotency_key) do nothing;

  select * into strict v_job
  from public.jobs
  where user_id = v_user and idempotency_key = v_key;

  if v_job.status in ('failed','cancelled') then
    select coalesce(max(attempt_no),0) into v_floor
    from public.job_attempts where job_id = v_job.id;
    update public.jobs set
      status='queued',
      attempt_count=greatest(attempt_count,v_floor),
      max_attempts=greatest(max_attempts,v_floor+3),
      next_run_at=now(),
      lease_owner=null,
      lease_expires_at=null,
      last_error_class=null,
      last_error_code=null,
      last_error_message=null,
      cancel_requested_at=null
    where id=v_job.id
    returning * into v_job;
  end if;

  return jsonb_build_object(
    'id',v_job.id,
    'status',v_job.status,
    'idempotencyKey',v_job.idempotency_key
  );
end;
$$;

revoke all on function public.enqueue_instagram_reel_cloud_ingestion(uuid)
  from public, anon, authenticated, service_role;
grant execute on function public.enqueue_instagram_reel_cloud_ingestion(uuid)
  to authenticated;

-- A ready Reel should enter the same Source Note pipeline as PDF/Threads.
-- Retrieval remains handled by the existing incremental index scheduler.
create or replace function public.enqueue_ready_instagram_reel_source_note()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_id uuid := gen_random_uuid();
  v_key text;
begin
  if new.source_type <> 'instagram_reel' or new.status <> 'ready'
     or new.active_revision_id is null then
    return new;
  end if;

  v_key := 'source_note:' || new.active_revision_id || ':source-note-v2-topics';
  insert into public.jobs(
    id,user_id,asset_id,job_type,payload_json,payload_schema_version,
    idempotency_key,resource_class,priority
  ) values (
    v_id,new.user_id,new.id,'generate_source_note',jsonb_build_object(
      'schemaVersion',1,
      'jobId',v_id,
      'userId',new.user_id,
      'jobType','generate_source_note',
      'idempotencyKey',v_key,
      'input',jsonb_build_object(
        'assetId',new.id,
        'revisionId',new.active_revision_id,
        'sourceType','instagram_reel'
      )
    ),1,v_key,'llm',100
  ) on conflict(user_id,idempotency_key) do nothing;
  return new;
end;
$$;

revoke all on function public.enqueue_ready_instagram_reel_source_note()
  from public, anon, authenticated, service_role;

drop trigger if exists assets_enqueue_ready_instagram_reel_source_note on public.assets;
create trigger assets_enqueue_ready_instagram_reel_source_note
after update of status, active_revision_id on public.assets
for each row
when (
  new.source_type = 'instagram_reel'
  and new.status = 'ready'
  and new.active_revision_id is not null
  and (
    old.status is distinct from new.status
    or old.active_revision_id is distinct from new.active_revision_id
  )
)
execute function public.enqueue_ready_instagram_reel_source_note();
