-- Every successfully published social source enters the existing Source Note
-- queue automatically. Manual enqueue RPCs remain available as idempotent retry
-- controls; no source data or existing Note is replaced.
set local lock_timeout = '5s';
set local statement_timeout = '2min';

create or replace function public.enqueue_ready_social_source_note()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job_id uuid := gen_random_uuid();
  v_key text;
begin
  if new.source_type not in ('threads', 'instagram_post', 'instagram_reel')
     or new.status <> 'ready'
     or new.active_revision_id is null then
    return new;
  end if;

  v_key := 'source_note:' || new.active_revision_id || ':source-note-v2-topics';
  insert into public.jobs (
    id, user_id, asset_id, job_type, payload_json, payload_schema_version,
    idempotency_key, resource_class, priority
  ) values (
    v_job_id, new.user_id, new.id, 'generate_source_note',
    jsonb_build_object(
      'schemaVersion', 1, 'jobId', v_job_id, 'userId', new.user_id,
      'jobType', 'generate_source_note', 'idempotencyKey', v_key,
      'input', jsonb_build_object(
        'assetId', new.id, 'revisionId', new.active_revision_id,
        'sourceType', new.source_type
      )
    ),
    1, v_key, 'llm', 100
  ) on conflict (user_id, idempotency_key) do nothing;

  return new;
end;
$$;

revoke all on function public.enqueue_ready_social_source_note()
  from public, anon, authenticated, service_role;

drop trigger if exists assets_enqueue_ready_instagram_reel_source_note
  on public.assets;
drop trigger if exists assets_enqueue_ready_social_source_note
  on public.assets;

create trigger assets_enqueue_ready_social_source_note
after update of status, active_revision_id on public.assets
for each row
when (
  new.source_type in ('threads', 'instagram_post', 'instagram_reel')
  and new.status = 'ready'
  and new.active_revision_id is not null
  and (
    old.status is distinct from new.status
    or old.active_revision_id is distinct from new.active_revision_id
  )
)
execute function public.enqueue_ready_social_source_note();

-- One-time idempotent backfill for social sources that became ready before the
-- generalized trigger existed. The unique (user_id, idempotency_key) constraint
-- prevents duplicate work for Reels or manually requested Source Notes.
with candidates as (
  select
    a.id as asset_id,
    a.user_id,
    a.source_type,
    a.active_revision_id,
    'source_note:' || a.active_revision_id || ':source-note-v2-topics' as job_key
  from public.assets a
  where a.source_type in ('threads', 'instagram_post', 'instagram_reel')
    and a.status = 'ready'
    and a.active_revision_id is not null
), prepared as (
  select candidates.*, gen_random_uuid() as job_id
  from candidates
)
insert into public.jobs (
  id, user_id, asset_id, job_type, payload_json, payload_schema_version,
  idempotency_key, resource_class, priority
)
select
  p.job_id, p.user_id, p.asset_id, 'generate_source_note',
  jsonb_build_object(
    'schemaVersion', 1, 'jobId', p.job_id, 'userId', p.user_id,
    'jobType', 'generate_source_note', 'idempotencyKey', p.job_key,
    'input', jsonb_build_object(
      'assetId', p.asset_id, 'revisionId', p.active_revision_id,
      'sourceType', p.source_type
    )
  ),
  1, p.job_key, 'llm', 100
from prepared p
on conflict (user_id, idempotency_key) do nothing;
