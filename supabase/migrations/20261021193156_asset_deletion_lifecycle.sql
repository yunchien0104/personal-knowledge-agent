-- Asset deletion is a fenced Queue workflow. The authenticated request RPC only
-- marks owned data unavailable and enqueues work; the existing Service Role
-- Worker is the sole Storage deletion coordinator.

alter table public.assets drop constraint assets_status_check;
alter table public.assets add constraint assets_status_check check (
  status in (
    'pending', 'processing', 'ready', 'failed', 'unsupported',
    'deleting', 'deletion_failed', 'source_removed'
  )
);

alter table public.jobs drop constraint jobs_resource_class_check;
alter table public.jobs add constraint jobs_resource_class_check check (
  resource_class in ('lightweight', 'media', 'llm', 'research', 'opportunity', 'deletion')
);

alter table public.jobs drop constraint jobs_payload_identity_check;
alter table public.jobs add constraint jobs_payload_identity_check check (
  payload_json ?& array[
    'schemaVersion', 'jobId', 'userId', 'jobType', 'idempotencyKey', 'input'
  ]
  and payload_json ->> 'schemaVersion' = payload_schema_version::text
  and jsonb_typeof(payload_json -> 'input') = 'object'
  and payload_json ->> 'jobId' = id::text
  and payload_json ->> 'userId' = user_id::text
  and payload_json ->> 'jobType' = job_type
  and payload_json ->> 'idempotencyKey' = idempotency_key
  and (
    (
      asset_id is not null
      and (payload_json -> 'input') ? 'assetId'
      and payload_json #>> '{input,assetId}' = asset_id::text
    )
    or (
      asset_id is null
      and job_type = 'execute_research'
      and not (payload_json -> 'input') ? 'assetId'
      and (payload_json -> 'input') ? 'researchRunId'
    )
    or (
      asset_id is null
      and job_type = 'rank_opportunities'
      and not (payload_json -> 'input') ? 'assetId'
      and (payload_json -> 'input') ? 'rankingDate'
    )
    or (
      asset_id is null
      and job_type = 'delete_asset'
      and (payload_json -> 'input') ?& array['assetId', 'deletionId', 'mode']
      and payload_json #>> '{input,mode}' in ('remove_original', 'purge_all')
    )
  )
);

create table public.asset_deletions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  asset_id uuid,
  asset_id_snapshot uuid not null,
  request_key uuid not null,
  mode text not null check (mode in ('remove_original', 'purge_all')),
  status text not null default 'pending' check (
    status in ('pending', 'deleting', 'completed', 'failed')
  ),
  job_id uuid,
  error_code text,
  error_message text,
  requested_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint asset_deletions_id_user_unique unique (id, user_id),
  constraint asset_deletions_user_request_unique unique (user_id, request_key),
  constraint asset_deletions_job_unique unique (job_id),
  constraint asset_deletions_asset_owner_fk
    foreign key (asset_id, user_id)
    references public.assets (id, user_id)
    on delete set null (asset_id),
  constraint asset_deletions_job_owner_fk
    foreign key (job_id, user_id)
    references public.jobs (id, user_id)
    on delete set null (job_id)
);

create unique index asset_deletions_one_active_per_asset
  on public.asset_deletions (user_id, asset_id_snapshot)
  where status in ('pending', 'deleting');
create index asset_deletions_user_updated_idx
  on public.asset_deletions (user_id, updated_at desc);

create trigger asset_deletions_set_updated_at
before update on public.asset_deletions
for each row execute function public.set_current_timestamp_updated_at();

alter table public.asset_deletions enable row level security;
alter table public.asset_deletions force row level security;
create policy asset_deletions_select_own on public.asset_deletions
for select to authenticated using ((select auth.uid()) = user_id);

revoke all on public.asset_deletions from anon, authenticated;
grant select on public.asset_deletions to authenticated;
grant select, insert, update, delete on public.asset_deletions to service_role;

create or replace function public.request_asset_deletion(
  p_asset_id uuid,
  p_mode text,
  p_request_key uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_asset public.assets%rowtype;
  v_deletion public.asset_deletions%rowtype;
  v_job_id uuid := gen_random_uuid();
  v_deletion_id uuid := gen_random_uuid();
  v_key text := 'delete_asset:' || p_request_key::text;
  v_evidence_ids uuid[];
  v_opportunity_ids uuid[];
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  if p_asset_id is null or p_request_key is null or p_mode is null
     or p_mode not in ('remove_original', 'purge_all') then
    raise invalid_parameter_value using message = 'asset deletion request is invalid';
  end if;

  select * into v_deletion from public.asset_deletions
  where user_id = v_user_id and request_key = p_request_key
  for update;
  if found then
    if v_deletion.asset_id_snapshot <> p_asset_id or v_deletion.mode <> p_mode then
      raise integrity_constraint_violation
        using message = 'request key conflicts with another deletion';
    end if;
    return jsonb_build_object(
      'id', v_deletion.id, 'assetId', v_deletion.asset_id_snapshot,
      'mode', v_deletion.mode, 'status', v_deletion.status,
      'jobId', v_deletion.job_id
    );
  end if;

  select * into v_asset from public.assets
  where id = p_asset_id and user_id = v_user_id and source_type = 'pdf'
  for update;
  if not found then
    raise insufficient_privilege using message = 'owned PDF asset not found';
  end if;
  if v_asset.status = 'source_removed' and p_mode = 'remove_original' then
    raise check_violation using message = 'original PDF has already been removed';
  end if;

  select * into v_deletion from public.asset_deletions
  where user_id = v_user_id and asset_id_snapshot = p_asset_id
    and status in ('pending', 'deleting')
  for update;
  if found then
    if v_deletion.mode <> p_mode then
      raise check_violation using message = 'another deletion mode is already active';
    end if;
    return jsonb_build_object(
      'id', v_deletion.id, 'assetId', v_deletion.asset_id_snapshot,
      'mode', v_deletion.mode, 'status', v_deletion.status,
      'jobId', v_deletion.job_id
    );
  end if;

  update public.job_attempts a set
    ended_at = coalesce(a.ended_at, now()),
    heartbeat_at = now(), outcome = coalesce(a.outcome, 'cancelled'),
    error_class = coalesce(a.error_class, 'permanent'),
    error_code = coalesce(a.error_code, 'asset_deletion_requested'),
    error_message = coalesce(a.error_message, 'Asset deletion invalidated this work.')
  from public.jobs j
  where a.job_id = j.id and j.user_id = v_user_id and j.asset_id = p_asset_id
    and a.ended_at is null;

  update public.jobs set
    status = 'cancelled', cancel_requested_at = coalesce(cancel_requested_at, now()),
    lease_owner = null, lease_expires_at = null, lease_generation = lease_generation + 1,
    last_error_class = 'permanent', last_error_code = 'asset_deletion_requested',
    last_error_message = 'Asset deletion invalidated this work.'
  where user_id = v_user_id and asset_id = p_asset_id
    and status in ('queued', 'running', 'retry_wait', 'paused_budget', 'paused_user');

  update public.assets set status = 'deleting', active_revision_id = null
  where id = p_asset_id and user_id = v_user_id;

  select coalesce(array_agg(e.id), '{}'::uuid[]) into v_evidence_ids
  from public.asset_evidence e
  join public.asset_revisions r
    on r.id = e.revision_id and r.user_id = e.user_id
  where r.asset_id = p_asset_id and e.user_id = v_user_id;

  update public.claim_evidence set
    evidence_id = null, source_revision_id = null, validity_status = 'source_deleted'
  where user_id = v_user_id and (
    evidence_id = any(v_evidence_ids) or evidence_id_snapshot = any(v_evidence_ids)
  );
  update public.research_citations set
    evidence_id = null, asset_id = null, source_revision_id = null
  where user_id = v_user_id
    and (asset_id = p_asset_id or asset_id_snapshot = p_asset_id);

  select coalesce(array_agg(id), '{}'::uuid[]) into v_opportunity_ids
  from public.research_opportunities
  where user_id = v_user_id and evidence_ids && v_evidence_ids;
  update public.research_opportunities ro set evidence_ids = array(
    select evidence_id from unnest(ro.evidence_ids) with ordinality ids(evidence_id, ordinal)
    where not (evidence_id = any(v_evidence_ids)) order by ordinal
  )
  where ro.id = any(v_opportunity_ids) and ro.user_id = v_user_id;
  delete from public.research_opportunities
  where user_id = v_user_id and id = any(v_opportunity_ids)
    and cardinality(evidence_ids) = 0;

  insert into public.jobs (
    id, user_id, asset_id, job_type, payload_json, payload_schema_version,
    idempotency_key, resource_class, priority, max_attempts
  ) values (
    v_job_id, v_user_id, null, 'delete_asset',
    jsonb_build_object(
      'schemaVersion', 1, 'jobId', v_job_id, 'userId', v_user_id,
      'jobType', 'delete_asset', 'idempotencyKey', v_key,
      'input', jsonb_build_object(
        'assetId', p_asset_id, 'deletionId', v_deletion_id, 'mode', p_mode,
        'storageBucket', 'asset-private'
      )
    ),
    1, v_key, 'deletion', 1000, 10
  );
  insert into public.asset_deletions (
    id, user_id, asset_id, asset_id_snapshot, request_key, mode, job_id
  ) values (
    v_deletion_id, v_user_id, p_asset_id, p_asset_id, p_request_key, p_mode, v_job_id
  ) returning * into v_deletion;

  return jsonb_build_object(
    'id', v_deletion.id, 'assetId', v_deletion.asset_id_snapshot,
    'mode', v_deletion.mode, 'status', v_deletion.status,
    'jobId', v_deletion.job_id
  );
end;
$$;

create or replace function public.get_asset_deletion_context(
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
  v_deletion public.asset_deletions%rowtype;
begin
  select * into v_job from public.jobs
  where id = p_job_id and job_type = 'delete_asset'
    and status = 'running' and lease_owner = p_worker_id
    and lease_generation = p_lease_generation and lease_expires_at > now();
  if not found then return null; end if;
  select * into v_deletion from public.asset_deletions
  where id = (v_job.payload_json #>> '{input,deletionId}')::uuid
    and user_id = v_job.user_id and job_id = v_job.id;
  if not found or v_deletion.status not in ('pending', 'deleting') then return null; end if;
  return jsonb_build_object(
    'deletionId', v_deletion.id,
    'assetId', v_deletion.asset_id_snapshot,
    'mode', v_deletion.mode,
    'storageBucket', 'asset-private',
    'storagePrefix', v_job.user_id::text || '/' || v_deletion.asset_id_snapshot::text
      || case when v_deletion.mode = 'remove_original' then '/original' else '' end
  );
end;
$$;

create or replace function public.complete_asset_deletion(
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
  v_deletion public.asset_deletions%rowtype;
  v_evidence_ids uuid[];
  v_opportunity_ids uuid[];
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'delete_asset' then return null; end if;
  select * into v_deletion from public.asset_deletions
  where id = (v_job.payload_json #>> '{input,deletionId}')::uuid
    and user_id = v_job.user_id and job_id = v_job.id
  for update;
  if not found then return null; end if;
  if v_job.status = 'completed' and v_deletion.status = 'completed' then
    return jsonb_build_object(
      'deletionId', v_deletion.id, 'status', 'completed', 'idempotent', true
    );
  end if;
  if v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then return null; end if;

  select coalesce(array_agg(e.id), '{}'::uuid[]) into v_evidence_ids
  from public.asset_evidence e
  join public.asset_revisions r
    on r.id = e.revision_id and r.user_id = e.user_id
  where r.asset_id = v_deletion.asset_id_snapshot and e.user_id = v_job.user_id;

  update public.claim_evidence set
    evidence_id = null, source_revision_id = null, validity_status = 'source_deleted'
  where user_id = v_job.user_id and (
    evidence_id = any(v_evidence_ids) or evidence_id_snapshot = any(v_evidence_ids)
  );
  update public.research_citations set
    evidence_id = null, asset_id = null, source_revision_id = null
  where user_id = v_job.user_id and (
    asset_id = v_deletion.asset_id_snapshot
    or asset_id_snapshot = v_deletion.asset_id_snapshot
  );
  select coalesce(array_agg(id), '{}'::uuid[]) into v_opportunity_ids
  from public.research_opportunities
  where user_id = v_job.user_id and evidence_ids && v_evidence_ids;
  update public.research_opportunities ro set evidence_ids = array(
    select evidence_id from unnest(ro.evidence_ids) with ordinality ids(evidence_id, ordinal)
    where not (evidence_id = any(v_evidence_ids)) order by ordinal
  )
  where ro.id = any(v_opportunity_ids) and ro.user_id = v_job.user_id;
  delete from public.research_opportunities
  where user_id = v_job.user_id and id = any(v_opportunity_ids)
    and cardinality(evidence_ids) = 0;

  if v_deletion.mode = 'remove_original' then
    update public.assets set status = 'source_removed', active_revision_id = null
    where id = v_deletion.asset_id_snapshot and user_id = v_job.user_id;
    if not found then
      raise foreign_key_violation using message = 'asset disappeared before deletion completed';
    end if;
  else
    delete from public.notes n
    where n.user_id = v_job.user_id and n.note_type = 'source_note'
      and exists (
        select 1 from public.source_note_generations g
        where g.note_id = n.id and g.user_id = n.user_id
          and g.asset_id_snapshot = v_deletion.asset_id_snapshot
      );
    delete from public.assets
    where id = v_deletion.asset_id_snapshot and user_id = v_job.user_id;
    if not found then
      raise foreign_key_violation using message = 'asset disappeared before purge completed';
    end if;
  end if;

  update public.asset_deletions set
    status = 'completed', completed_at = now(), error_code = null, error_message = null
  where id = v_deletion.id;
  update public.job_attempts set
    ended_at = now(), heartbeat_at = now(), outcome = 'completed'
  where job_id = v_job.id and lease_generation = p_lease_generation
    and worker_id = p_worker_id and ended_at is null;
  update public.jobs set
    status = 'completed', lease_owner = null, lease_expires_at = null,
    checkpoint_ref = v_deletion.id::text,
    last_error_class = null, last_error_code = null, last_error_message = null
  where id = v_job.id;
  return jsonb_build_object(
    'deletionId', v_deletion.id, 'status', 'completed', 'idempotent', false
  );
end;
$$;

create or replace function public.sync_asset_deletion_job_status()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if new.job_type = 'delete_asset' and new.status is distinct from old.status then
    update public.asset_deletions set
      status = case
        when new.status = 'running' then 'deleting'
        when new.status in ('queued', 'retry_wait') then 'pending'
        when new.status in ('failed', 'cancelled') then 'failed'
        when new.status = 'completed' then 'completed'
        else status
      end,
      started_at = case when new.status = 'running' then coalesce(started_at, now()) else started_at end,
      completed_at = case when new.status = 'completed' then coalesce(completed_at, now()) else completed_at end,
      error_code = case when new.status in ('failed', 'cancelled') then new.last_error_code else error_code end,
      error_message = case when new.status in ('failed', 'cancelled') then new.last_error_message else error_message end
    where job_id = new.id;
    if new.status in ('failed', 'cancelled') then
      update public.assets set status = 'deletion_failed', active_revision_id = null
      where id = (new.payload_json #>> '{input,assetId}')::uuid
        and user_id = new.user_id and status = 'deleting';
    end if;
  end if;
  return new;
end;
$$;

revoke all on function public.sync_asset_deletion_job_status() from public;
create trigger jobs_sync_asset_deletion_status
after update of status on public.jobs
for each row execute function public.sync_asset_deletion_job_status();

-- Latest Queue claimant plus the deletion capability. A delete request has
-- already fenced and cancelled all Asset-bound work before this job is visible.
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
  if p_resource_class is not null and p_resource_class not in (
    'lightweight', 'media', 'llm', 'research', 'opportunity', 'deletion'
  ) then
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
      priority + least(
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
      update public.jobs set
        status = 'failed', lease_owner = null, lease_expires_at = null,
        last_error_class = 'permanent', last_error_code = 'attempts_exhausted',
        last_error_message = 'Maximum processing attempts were exhausted.'
      where id = v_job.id;
      update public.job_attempts set
        ended_at = coalesce(ended_at, now()),
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
      update public.job_attempts set
        ended_at = now(), outcome = 'lease_expired', error_class = 'transient',
        error_code = 'lease_expired',
        error_message = 'Worker lease expired before completion.'
      where job_id = v_job.id and lease_generation = v_job.lease_generation
        and ended_at is null;
    end if;

    update public.jobs set
      status = 'running', attempt_count = attempt_count + 1,
      lease_owner = p_worker_id,
      lease_expires_at = now() + make_interval(secs => p_lease_seconds),
      lease_generation = lease_generation + 1
    where id = v_job.id returning * into v_job;
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

revoke execute on function public.request_asset_deletion(uuid,text,uuid)
  from public, anon, authenticated, service_role;
revoke execute on function public.get_asset_deletion_context(uuid,text,bigint)
  from public, anon, authenticated, service_role;
revoke execute on function public.complete_asset_deletion(uuid,text,bigint)
  from public, anon, authenticated, service_role;
revoke execute on function public.claim_job(text,text,integer)
  from public, anon, authenticated, service_role;
grant execute on function public.request_asset_deletion(uuid,text,uuid) to authenticated;
grant execute on function public.get_asset_deletion_context(uuid,text,bigint) to service_role;
grant execute on function public.complete_asset_deletion(uuid,text,bigint) to service_role;
grant execute on function public.claim_job(text,text,integer) to service_role;

comment on table public.asset_deletions is
  'Application deletion audit/status records. Completed rows may be retained for operational audit; database backups follow the Supabase project retention policy and are not synchronously erased by this workflow.';

-- Retrieval must never surface a deleting, failed-deletion, or source-removed
-- Asset. Generated Notes remain stored and visible in Library, but cease to be
-- retrieval evidence when their immutable source Revision is no longer active.
create or replace function public.search_knowledge(
  p_query text,
  p_limit integer default 20,
  p_offset integer default 0
)
returns table (
  result_type text,
  content_origin text,
  result_id uuid,
  title text,
  snippet text,
  relevance real,
  asset_id uuid,
  revision_id uuid,
  note_id uuid,
  claim_id uuid,
  citations jsonb,
  created_at timestamptz,
  total_count bigint
)
language plpgsql
stable
security invoker
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_query text := btrim(p_query);
  v_tsquery tsquery;
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  if v_query is null or char_length(v_query) not between 2 and 200
     or p_limit is null or p_limit not between 1 and 50
     or p_offset is null or p_offset not between 0 and 1000 then
    raise invalid_parameter_value using message = 'retrieval parameters are invalid';
  end if;
  v_tsquery := websearch_to_tsquery('simple', v_query);
  if numnode(v_tsquery) = 0 then return; end if;

  return query
  with note_matches as (
    select
      'source_note'::text as result_type,
      'generated_note'::text as content_origin,
      n.id as result_id,
      n.title,
      left(n.body, 1000) as snippet,
      ts_rank_cd(
        setweight(to_tsvector('simple', coalesce(n.title, '')), 'A')
          || setweight(to_tsvector('simple', coalesce(n.body, '')), 'B'),
        v_tsquery
      )::real as relevance,
      g.asset_id_snapshot as asset_id,
      g.source_revision_id_snapshot as revision_id,
      n.id as note_id,
      null::uuid as claim_id,
      coalesce(citation_set.citations, '[]'::jsonb) as citations,
      n.updated_at as created_at
    from public.notes n
    join public.source_note_generations g
      on g.note_id = n.id and g.user_id = n.user_id
    join public.assets a
      on a.id = g.asset_id_snapshot and a.user_id = g.user_id
      and a.status = 'ready' and a.active_revision_id = g.source_revision_id_snapshot
    left join lateral (
      select jsonb_agg(item.citation order by item.created_at) as citations
      from (
        select jsonb_build_object(
          'assetId', g.asset_id_snapshot,
          'revisionId', ce.source_revision_id_snapshot,
          'evidenceId', ce.evidence_id_snapshot,
          'locator', ce.locator_json,
          'validityStatus', case
            when ce.evidence_id is null then 'source_deleted'
            else ce.validity_status
          end
        ) as citation, ce.created_at
        from public.claims c
        join public.claim_evidence ce
          on ce.claim_id = c.id and ce.user_id = c.user_id
        where c.note_id = n.id and c.user_id = v_user_id
        order by ce.created_at
        limit 20
      ) item
    ) citation_set on true
    where n.user_id = v_user_id and n.note_type = 'source_note'
      and to_tsvector('simple', coalesce(n.title, '') || ' ' || coalesce(n.body, ''))
          @@ v_tsquery
  ),
  claim_matches as (
    select
      'claim'::text as result_type,
      'generated_claim'::text as content_origin,
      c.id as result_id,
      n.title,
      left(c.text, 1000) as snippet,
      ts_rank_cd(to_tsvector('simple', coalesce(c.text, '')), v_tsquery)::real
        as relevance,
      g.asset_id_snapshot as asset_id,
      g.source_revision_id_snapshot as revision_id,
      c.note_id,
      c.id as claim_id,
      coalesce(citation_set.citations, '[]'::jsonb) as citations,
      c.created_at
    from public.claims c
    join public.notes n on n.id = c.note_id and n.user_id = c.user_id
    join public.source_note_generations g
      on g.note_id = c.note_id and g.user_id = c.user_id
    join public.assets a
      on a.id = g.asset_id_snapshot and a.user_id = g.user_id
      and a.status = 'ready' and a.active_revision_id = g.source_revision_id_snapshot
    left join lateral (
      select jsonb_agg(jsonb_build_object(
        'assetId', g.asset_id_snapshot,
        'revisionId', ce.source_revision_id_snapshot,
        'evidenceId', ce.evidence_id_snapshot,
        'locator', ce.locator_json,
        'validityStatus', case
          when ce.evidence_id is null then 'source_deleted'
          else ce.validity_status
        end
      ) order by ce.created_at) as citations
      from (
        select ce_source.* from public.claim_evidence ce_source
        where ce_source.claim_id = c.id and ce_source.user_id = v_user_id
        order by ce_source.created_at
        limit 20
      ) ce
    ) citation_set on true
    where c.user_id = v_user_id
      and to_tsvector('simple', coalesce(c.text, '')) @@ v_tsquery
  ),
  evidence_matches as (
    select
      'evidence'::text as result_type,
      'original_source'::text as content_origin,
      e.id as result_id,
      'PDF source page ' || coalesce(e.locator_json ->> 'page_number', '?') as title,
      left(e.excerpt, 1000) as snippet,
      ts_rank_cd(to_tsvector('simple', coalesce(e.excerpt, '')), v_tsquery)::real
        as relevance,
      r.asset_id,
      e.revision_id,
      null::uuid as note_id,
      null::uuid as claim_id,
      jsonb_build_array(jsonb_build_object(
        'assetId', r.asset_id,
        'revisionId', e.revision_id,
        'evidenceId', e.id,
        'locator', e.locator_json,
        'validityStatus', 'valid'
      )) as citations,
      e.created_at
    from public.asset_evidence e
    join public.asset_revisions r
      on r.id = e.revision_id and r.user_id = e.user_id
    join public.assets a
      on a.id = r.asset_id and a.user_id = r.user_id
      and a.status = 'ready' and a.active_revision_id = r.id
    where e.user_id = v_user_id
      and to_tsvector('simple', coalesce(e.excerpt, '')) @@ v_tsquery
  ),
  matches as (
    select * from note_matches
    union all select * from claim_matches
    union all select * from evidence_matches
  )
  select
    m.result_type, m.content_origin, m.result_id, m.title, m.snippet,
    m.relevance, m.asset_id, m.revision_id, m.note_id, m.claim_id,
    m.citations, m.created_at, count(*) over() as total_count
  from matches m
  order by m.relevance desc, m.created_at desc, m.result_id
  limit p_limit offset p_offset;
end;
$$;

revoke all on function public.search_knowledge(text,integer,integer)
  from public, anon, authenticated, service_role;
grant execute on function public.search_knowledge(text,integer,integer)
  to authenticated, service_role;
