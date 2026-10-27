-- User-owned Topics and a note-only knowledge projection. Topic suggestions are
-- stored separately from final assignments so manual edits (including an empty
-- selection) are authoritative over delayed/background AI results.

create table public.topics (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  name text not null check (char_length(btrim(name)) between 1 and 80),
  description text not null default '' check (char_length(description) <= 500),
  is_seed boolean not null default false,
  merged_into_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint topics_id_user_unique unique (id, user_id),
  constraint topics_merge_owner_fk foreign key (merged_into_id, user_id)
    references public.topics (id, user_id) on delete restrict,
  constraint topics_not_self_merged check (merged_into_id is null or merged_into_id <> id)
);

create unique index topics_user_active_name_unique
  on public.topics (user_id, lower(btrim(name))) where merged_into_id is null;
create index topics_user_active_idx on public.topics (user_id, created_at, id)
  where merged_into_id is null;

create table public.note_topic_states (
  note_id uuid primary key,
  user_id uuid not null,
  ai_suggested_topic_ids uuid[] not null default '{}',
  classification_status text not null default 'unclassified' check (
    classification_status in ('pending', 'classified', 'unclassified', 'failed')
  ),
  classification_error text,
  manual_override boolean not null default false,
  manually_updated_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint note_topic_states_note_owner_fk foreign key (note_id, user_id)
    references public.notes (id, user_id) on delete cascade
);
create index note_topic_states_user_status_idx
  on public.note_topic_states (user_id, classification_status, manual_override);

create table public.note_topics (
  note_id uuid not null,
  topic_id uuid not null,
  user_id uuid not null,
  assignment_source text not null check (assignment_source in ('ai', 'user')),
  created_at timestamptz not null default now(),
  primary key (note_id, topic_id),
  constraint note_topics_note_owner_fk foreign key (note_id, user_id)
    references public.notes (id, user_id) on delete cascade,
  constraint note_topics_topic_owner_fk foreign key (topic_id, user_id)
    references public.topics (id, user_id) on delete cascade
);
create index note_topics_user_topic_note_idx on public.note_topics (user_id, topic_id, note_id);

create trigger topics_set_updated_at before update on public.topics
for each row execute function public.set_current_timestamp_updated_at();
create trigger note_topic_states_set_updated_at before update on public.note_topic_states
for each row execute function public.set_current_timestamp_updated_at();

alter table public.topics enable row level security;
alter table public.topics force row level security;
alter table public.note_topic_states enable row level security;
alter table public.note_topic_states force row level security;
alter table public.note_topics enable row level security;
alter table public.note_topics force row level security;

create policy topics_select_own on public.topics for select to authenticated
using ((select auth.uid()) = user_id);
create policy topics_insert_own on public.topics for insert to authenticated
with check ((select auth.uid()) = user_id and merged_into_id is null);
create policy topics_update_own on public.topics for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy note_topic_states_select_own on public.note_topic_states for select to authenticated
using ((select auth.uid()) = user_id);
create policy note_topics_select_own on public.note_topics for select to authenticated
using ((select auth.uid()) = user_id);

revoke all on public.topics, public.note_topic_states, public.note_topics from anon, authenticated;
grant select on public.topics to authenticated;
grant insert (id, user_id, name, description) on public.topics to authenticated;
grant update (name, description) on public.topics to authenticated;
grant select on public.note_topic_states, public.note_topics to authenticated;
grant select, insert, update, delete on public.topics, public.note_topic_states, public.note_topics
  to service_role;

-- Use a prompt-specific idempotency key for topic-aware Source Notes. Manual
-- retries retain attempt history instead of resetting attempt_count and
-- colliding with the existing job_attempts(job_id, attempt_no) rows.
create or replace function public.enqueue_source_note_generation(p_asset_id uuid)
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
  where id = p_asset_id and user_id = v_user_id and source_type = 'pdf';
  if not found or v_asset.status <> 'ready' or v_asset.active_revision_id is null then
    raise check_violation using message = 'ready owned PDF asset required';
  end if;
  select * into strict v_revision from public.asset_revisions
  where id = v_asset.active_revision_id and user_id = v_user_id
    and asset_id = v_asset.id and status = 'validated';
  if not exists (
    select 1 from public.asset_artifacts
    where revision_id = v_revision.id and user_id = v_user_id and kind = 'native_pdf_text'
  ) or not exists (
    select 1 from public.asset_evidence
    where revision_id = v_revision.id and user_id = v_user_id and locator_type = 'pdf'
  ) then
    raise check_violation using message = 'validated PDF evidence required';
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
      'input', jsonb_build_object('assetId', v_asset.id, 'revisionId', v_revision.id)
    ),
    1, v_key, 'llm', 100
  ) on conflict (user_id, idempotency_key) do nothing;

  select * into strict v_job from public.jobs
  where user_id = v_user_id and idempotency_key = v_key;
  if v_job.status in ('failed', 'cancelled') then
    select coalesce(max(attempt_no), 0) into v_attempt_floor
    from public.job_attempts where job_id = v_job.id;
    update public.jobs set
      status = 'queued',
      attempt_count = greatest(attempt_count, v_attempt_floor),
      max_attempts = greatest(max_attempts, v_attempt_floor + 3),
      next_run_at = now(), lease_owner = null, lease_expires_at = null,
      last_error_class = null, last_error_code = null, last_error_message = null,
      cancel_requested_at = null
    where id = v_job.id returning * into v_job;
  end if;
  return jsonb_build_object('id', v_job.id, 'status', v_job.status);
end;
$$;

revoke all on function public.enqueue_source_note_generation(uuid)
  from public, anon, authenticated;
grant execute on function public.enqueue_source_note_generation(uuid) to authenticated;

create or replace function public.default_topic_catalog()
returns table (name text, description text)
language sql
immutable
security invoker
set search_path = ''
as $$ values
  ('科技與產品', '軟硬體、網路服務、AI、產品設計與數位趨勢'),
  ('金融與投資', '金融市場、公司財務、投資工具與個人理財'),
  ('旅遊與地方', '目的地、行程、交通、住宿與地方觀察'),
  ('飲食與料理', '餐飲、食材、食譜、咖啡與飲食文化'),
  ('學習與知識', '課程、閱讀、研究方法、技能與知識整理'),
  ('健康與生活', '健康、運動、心理、居家與日常生活'),
  ('工作與管理', '職涯、組織、商業、管理與生產力'),
  ('文化與娛樂', '藝術、設計、影視、音樂、遊戲與流行文化')
$$;
revoke all on function public.default_topic_catalog() from public, anon, authenticated;
grant execute on function public.default_topic_catalog() to service_role;

create or replace function public.ensure_default_topics()
returns setof public.topics
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  insert into public.topics (user_id, name, description, is_seed)
  select v_user_id, seed.name, seed.description, true
  from public.default_topic_catalog() seed
  where not exists (
    select 1 from public.topics t
    where t.user_id = v_user_id and lower(btrim(t.name)) = lower(seed.name)
      and t.merged_into_id is null
  );
  return query select * from public.topics
    where user_id = v_user_id and merged_into_id is null
    order by is_seed desc, name, id;
end;
$$;

create or replace function public.set_note_topics(p_note_id uuid, p_topic_ids uuid[])
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_ids uuid[] := coalesce(p_topic_ids, '{}'::uuid[]);
begin
  if v_user_id is null then raise insufficient_privilege using message = 'authentication required'; end if;
  if not exists (select 1 from public.notes where id = p_note_id and user_id = v_user_id) then
    raise insufficient_privilege using message = 'owned note not found';
  end if;
  if cardinality(v_ids) > 20 or cardinality(v_ids) <> cardinality(array(select distinct x from unnest(v_ids) x))
     or exists (select 1 from unnest(v_ids) x where not exists (
       select 1 from public.topics t where t.id = x and t.user_id = v_user_id and t.merged_into_id is null
     )) then
    raise invalid_parameter_value using message = 'topic selection is invalid';
  end if;
  delete from public.note_topics where note_id = p_note_id and user_id = v_user_id;
  insert into public.note_topics (note_id, topic_id, user_id, assignment_source)
    select p_note_id, x, v_user_id, 'user' from unnest(v_ids) x;
  insert into public.note_topic_states (
    note_id, user_id, classification_status, manual_override, manually_updated_at
  ) values (
    p_note_id, v_user_id,
    case when cardinality(v_ids) = 0 then 'unclassified' else 'classified' end,
    true, now()
  ) on conflict (note_id) do update set
    classification_status = excluded.classification_status,
    classification_error = null,
    manual_override = true,
    manually_updated_at = now();
  return jsonb_build_object('noteId', p_note_id, 'topicIds', to_jsonb(v_ids), 'manualOverride', true);
end;
$$;

create or replace function public.get_topic_classification_options(p_user_id uuid)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if p_user_id is null then
    raise invalid_parameter_value using message = 'valid user required';
  end if;
  insert into public.topics (user_id, name, description, is_seed)
  select p_user_id, seed.name, seed.description, true
  from public.default_topic_catalog() seed
  where not exists (
    select 1 from public.topics t where t.user_id = p_user_id
      and lower(btrim(t.name)) = lower(seed.name) and t.merged_into_id is null
  );
  return coalesce((select jsonb_agg(jsonb_build_object(
    'id', id, 'name', name, 'description', description
  ) order by is_seed desc, name, id) from public.topics
    where user_id = p_user_id and merged_into_id is null), '[]'::jsonb);
end;
$$;

create or replace function public.apply_ai_note_topics(
  p_note_id uuid, p_topic_ids uuid[], p_status text, p_error text default null
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_note public.notes%rowtype;
  v_ids uuid[] := coalesce(p_topic_ids, '{}'::uuid[]);
  v_manual boolean;
begin
  select * into v_note from public.notes where id = p_note_id;
  if not found then return null; end if;
  if p_status not in ('classified', 'unclassified', 'failed') then
    raise invalid_parameter_value using message = 'classification status is invalid';
  end if;
  if cardinality(v_ids) > 20 or cardinality(v_ids) <> cardinality(array(select distinct x from unnest(v_ids) x))
     or exists (select 1 from unnest(v_ids) x where not exists (
       select 1 from public.topics t where t.id = x and t.user_id = v_note.user_id and t.merged_into_id is null
     )) then
    v_ids := '{}'::uuid[];
    p_status := 'failed';
    p_error := 'invalid_topic_ids';
  end if;
  insert into public.note_topic_states (
    note_id, user_id, ai_suggested_topic_ids, classification_status, classification_error
  ) values (p_note_id, v_note.user_id, v_ids, p_status, left(p_error, 240))
  on conflict (note_id) do update set
    ai_suggested_topic_ids = excluded.ai_suggested_topic_ids,
    classification_status = excluded.classification_status,
    classification_error = excluded.classification_error
  returning manual_override into v_manual;
  if not v_manual then
    delete from public.note_topics where note_id = p_note_id and user_id = v_note.user_id;
    insert into public.note_topics (note_id, topic_id, user_id, assignment_source)
      select p_note_id, x, v_note.user_id, 'ai' from unnest(v_ids) x
      on conflict do nothing;
  end if;
  return jsonb_build_object('noteId', p_note_id, 'topicIds', to_jsonb(v_ids),
    'manualOverride', v_manual, 'status', p_status);
end;
$$;

create or replace function public.merge_topics(p_source_topic_id uuid, p_target_topic_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare v_user_id uuid := auth.uid();
begin
  if v_user_id is null then raise insufficient_privilege using message = 'authentication required'; end if;
  if p_source_topic_id = p_target_topic_id or not exists (
    select 1 from public.topics where id = p_source_topic_id and user_id = v_user_id and merged_into_id is null
  ) or not exists (
    select 1 from public.topics where id = p_target_topic_id and user_id = v_user_id and merged_into_id is null
  ) then raise invalid_parameter_value using message = 'active owned topics required'; end if;
  insert into public.note_topics (note_id, topic_id, user_id, assignment_source, created_at)
    select note_id, p_target_topic_id, user_id, assignment_source, created_at
    from public.note_topics where topic_id = p_source_topic_id and user_id = v_user_id
    on conflict (note_id, topic_id) do update set assignment_source = case
      when public.note_topics.assignment_source = 'user' or excluded.assignment_source = 'user' then 'user' else 'ai' end;
  delete from public.note_topics where topic_id = p_source_topic_id and user_id = v_user_id;
  update public.note_topic_states set ai_suggested_topic_ids = array(
    select distinct case when x = p_source_topic_id then p_target_topic_id else x end
    from unnest(ai_suggested_topic_ids) x order by 1
  ) where user_id = v_user_id and p_source_topic_id = any(ai_suggested_topic_ids);
  update public.topics set merged_into_id = p_target_topic_id
    where id = p_source_topic_id and user_id = v_user_id;
  return jsonb_build_object('sourceTopicId', p_source_topic_id, 'targetTopicId', p_target_topic_id);
end;
$$;

create or replace function public.list_knowledge_notes(
  p_search text default null,
  p_topic_id uuid default null,
  p_unclassified boolean default false,
  p_limit integer default 24,
  p_offset integer default 0
)
returns table (
  id uuid, note_type text, title text, summary text, publication_state text,
  created_at timestamptz, updated_at timestamptz, topics jsonb,
  manual_topic_override boolean, source_asset_id uuid, source_status text,
  total_count bigint
)
language sql
security invoker
set search_path = ''
as $$
  with ranked as (
    select n.*, g.asset_id_snapshot,
      case when n.note_type = 'source_note' then
        row_number() over (partition by g.asset_id_snapshot order by g.created_at desc, n.updated_at desc, n.id desc)
      else 1 end as version_rank
    from public.notes n
    left join public.source_note_generations g on g.note_id = n.id and g.user_id = n.user_id
    where n.user_id = (select auth.uid()) and n.publication_state <> 'archived'
  ), eligible as (
    select r.*, a.status as asset_status
    from ranked r left join public.assets a
      on a.id = r.asset_id_snapshot and a.user_id = r.user_id
    where r.version_rank = 1
      and (p_search is null or btrim(p_search) = '' or
        r.title ilike '%' || p_search || '%' or r.body ilike '%' || p_search || '%')
      and (p_topic_id is null or exists (
        select 1 from public.note_topics nt where nt.note_id = r.id and nt.user_id = r.user_id
          and nt.topic_id = p_topic_id
      ))
      and (not p_unclassified or not exists (
        select 1 from public.note_topics nt where nt.note_id = r.id and nt.user_id = r.user_id
      ))
  )
  select e.id, e.note_type, e.title,
    left(btrim(regexp_replace(e.body, E'[#*_`>\\[\\]()]', '', 'g')), 420) as summary,
    e.publication_state, e.created_at, e.updated_at,
    coalesce((select jsonb_agg(jsonb_build_object('id', t.id, 'name', t.name)
      order by t.name) from public.note_topics nt join public.topics t
      on t.id = nt.topic_id and t.user_id = nt.user_id
      where nt.note_id = e.id and nt.user_id = e.user_id and t.merged_into_id is null), '[]'::jsonb),
    coalesce((select s.manual_override from public.note_topic_states s where s.note_id = e.id), false),
    e.asset_id_snapshot, e.asset_status, count(*) over ()
  from eligible e order by e.updated_at desc, e.id desc
  limit greatest(1, least(p_limit, 100)) offset greatest(p_offset, 0);
$$;

create or replace function public.get_topic_backfill_candidates(p_limit integer default 25)
returns jsonb
language sql
security invoker
set search_path = ''
as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'noteId', q.id, 'userId', q.user_id, 'title', q.title, 'body', q.body,
    'availableTopics', q.available_topics
  ) order by q.updated_at, q.id), '[]'::jsonb)
  from (
    select n.id, n.user_id, n.title, n.body, n.updated_at,
      (select coalesce(jsonb_agg(jsonb_build_object(
        'id', t.id, 'name', t.name, 'description', t.description
      ) order by t.name), '[]'::jsonb) from public.topics t
       where t.user_id = n.user_id and t.merged_into_id is null) as available_topics
    from public.notes n
    left join public.note_topic_states s on s.note_id = n.id and s.user_id = n.user_id
    where n.publication_state <> 'archived'
      and coalesce(s.manual_override, false) = false
      and not exists (select 1 from public.note_topics nt where nt.note_id = n.id)
      and coalesce(s.classification_status, 'unclassified') in ('unclassified', 'failed')
    order by n.updated_at, n.id
    limit greatest(1, least(p_limit, 100))
  ) q;
$$;

-- Local and hosted Supabase may grant EXECUTE directly to API roles through
-- default privileges. Revoke those explicit grants as well as PUBLIC before
-- exposing only the intended RPC surface below.
revoke all on function public.ensure_default_topics() from public, anon, authenticated;
revoke all on function public.set_note_topics(uuid,uuid[]) from public, anon, authenticated;
revoke all on function public.get_topic_classification_options(uuid) from public, anon, authenticated;
revoke all on function public.apply_ai_note_topics(uuid,uuid[],text,text) from public, anon, authenticated;
revoke all on function public.merge_topics(uuid,uuid) from public, anon, authenticated;
revoke all on function public.list_knowledge_notes(text,uuid,boolean,integer,integer) from public, anon, authenticated;
revoke all on function public.get_topic_backfill_candidates(integer) from public, anon, authenticated;
grant execute on function public.ensure_default_topics() to authenticated;
grant execute on function public.set_note_topics(uuid,uuid[]) to authenticated;
grant execute on function public.merge_topics(uuid,uuid) to authenticated;
grant execute on function public.list_knowledge_notes(text,uuid,boolean,integer,integer) to authenticated;
grant execute on function public.apply_ai_note_topics(uuid,uuid[],text,text) to service_role;
grant execute on function public.get_topic_classification_options(uuid) to service_role;
grant execute on function public.get_topic_backfill_candidates(integer) to service_role;
