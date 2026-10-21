-- Step 5A: immutable-source, editable-note publication for grounded PDF notes.

alter table public.jobs drop constraint jobs_resource_class_check;
alter table public.jobs add constraint jobs_resource_class_check
  check (resource_class in ('lightweight', 'media', 'llm'));

create table public.source_note_generations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  note_id uuid not null,
  asset_id uuid references public.assets (id) on delete set null,
  source_revision_id uuid references public.asset_revisions (id) on delete set null,
  asset_id_snapshot uuid not null,
  source_revision_id_snapshot uuid not null,
  source_fingerprint text not null check (source_fingerprint ~ '^[0-9a-f]{64}$'),
  job_id uuid not null unique,
  model_provider text not null check (char_length(model_provider) between 1 and 64),
  model_name text not null check (char_length(model_name) between 1 and 128),
  prompt_version text not null check (prompt_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
  output_hash text not null check (output_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  constraint source_note_generations_note_owner_fk
    foreign key (note_id, user_id) references public.notes (id, user_id) on delete cascade,
  constraint source_note_generations_note_unique unique (note_id)
);
create index source_note_generations_user_revision_idx
  on public.source_note_generations (user_id, source_revision_id_snapshot, created_at desc);
create index source_note_generations_asset_fk_idx on public.source_note_generations (asset_id);
create index source_note_generations_revision_fk_idx
  on public.source_note_generations (source_revision_id);

create table public.claims (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  note_id uuid not null,
  text text not null check (char_length(text) between 1 and 4000),
  claim_type text not null check (
    claim_type in ('source_assertion', 'forecast', 'inference', 'hypothesis')
  ),
  verification_status text not null default 'unreviewed' check (
    verification_status in (
      'unreviewed', 'insufficient_evidence', 'partially_supported',
      'corroborated', 'disputed', 'outdated'
    )
  ),
  needs_review boolean not null default true,
  source_note_revision integer not null check (source_note_revision > 0),
  created_at timestamptz not null default now(),
  constraint claims_note_owner_fk
    foreign key (note_id, user_id) references public.notes (id, user_id) on delete cascade,
  constraint claims_id_user_id_unique unique (id, user_id)
);
create index claims_user_note_idx on public.claims (user_id, note_id, created_at);

create table public.claim_evidence (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  claim_id uuid not null,
  evidence_id uuid references public.asset_evidence (id) on delete set null,
  evidence_id_snapshot uuid not null,
  source_revision_id uuid references public.asset_revisions (id) on delete set null,
  source_revision_id_snapshot uuid not null,
  relation_type text not null check (
    relation_type in ('asserts', 'supports', 'contradicts', 'contextualizes', 'mentions')
  ),
  validity_status text not null default 'valid' check (
    validity_status in ('valid', 'source_deleted', 'revision_inactive', 'invalid')
  ),
  locator_json jsonb not null check (jsonb_typeof(locator_json) = 'object'),
  supporting_quote text not null check (char_length(supporting_quote) between 1 and 4000),
  created_at timestamptz not null default now(),
  constraint claim_evidence_claim_owner_fk
    foreign key (claim_id, user_id) references public.claims (id, user_id) on delete cascade,
  constraint claim_evidence_claim_source_unique unique (claim_id, evidence_id_snapshot)
);
create index claim_evidence_user_evidence_idx
  on public.claim_evidence (user_id, evidence_id_snapshot);
create index claim_evidence_live_evidence_fk_idx on public.claim_evidence (evidence_id);
create index claim_evidence_live_revision_fk_idx on public.claim_evidence (source_revision_id);

alter table public.source_note_generations enable row level security;
alter table public.source_note_generations force row level security;
alter table public.claims enable row level security;
alter table public.claims force row level security;
alter table public.claim_evidence enable row level security;
alter table public.claim_evidence force row level security;

create policy source_note_generations_select_own on public.source_note_generations
for select to authenticated using ((select auth.uid()) = user_id);
create policy claims_select_own on public.claims
for select to authenticated using ((select auth.uid()) = user_id);
create policy claim_evidence_select_own on public.claim_evidence
for select to authenticated using ((select auth.uid()) = user_id);

revoke all on public.source_note_generations, public.claims, public.claim_evidence
  from anon, authenticated;
grant select on public.source_note_generations, public.claims, public.claim_evidence
  to authenticated;
grant select, insert, update, delete
  on public.source_note_generations, public.claims, public.claim_evidence
  to service_role;

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

  v_key := 'source_note:' || v_revision.id::text || ':prompt-v1';
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
    update public.jobs set
      status = 'queued', attempt_count = 0, next_run_at = now(),
      last_error_class = null, last_error_code = null, last_error_message = null,
      cancel_requested_at = null
    where id = v_job.id returning * into v_job;
  end if;
  return jsonb_build_object('id', v_job.id, 'status', v_job.status);
end;
$$;

revoke all on function public.enqueue_source_note_generation(uuid) from public;
grant execute on function public.enqueue_source_note_generation(uuid) to authenticated;

create or replace function public.update_note_content(
  p_note_id uuid, p_expected_revision integer, p_title text, p_body text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_note public.notes%rowtype;
begin
  if v_user_id is null then
    raise insufficient_privilege using message = 'authentication required';
  end if;
  if p_title is null or char_length(p_title) not between 1 and 300
     or p_body is null or char_length(p_body) > 100000
     or p_expected_revision is null or p_expected_revision < 1 then
    raise invalid_parameter_value using message = 'note content is invalid';
  end if;
  update public.notes set
    title = p_title, body = p_body, body_revision = body_revision + 1
  where id = p_note_id and user_id = v_user_id and body_revision = p_expected_revision
  returning * into v_note;
  if not found then return null; end if;
  return to_jsonb(v_note);
end;
$$;

revoke all on function public.update_note_content(uuid,integer,text,text) from public;
grant execute on function public.update_note_content(uuid,integer,text,text) to authenticated;

create or replace function public.get_source_note_context(
  p_job_id uuid, p_worker_id text, p_lease_generation bigint
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_revision public.asset_revisions%rowtype;
  v_object_key text;
begin
  select * into v_job from public.jobs where id = p_job_id;
  if not found or v_job.job_type <> 'generate_source_note'
     or v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then
    return null;
  end if;
  select * into v_revision from public.asset_revisions
  where id = (v_job.payload_json #>> '{input,revisionId}')::uuid
    and id = (select active_revision_id from public.assets where id = v_job.asset_id)
    and asset_id = v_job.asset_id and user_id = v_job.user_id and status = 'validated';
  if not found then return null; end if;
  select object_key into v_object_key from public.asset_artifacts
  where revision_id = v_revision.id and user_id = v_job.user_id and kind = 'native_pdf_text'
  order by created_at desc limit 1;
  if v_object_key is null then return null; end if;
  return jsonb_build_object(
    'assetId', v_job.asset_id, 'revisionId', v_revision.id,
    'sourceFingerprint', v_revision.fingerprint, 'artifactObjectKey', v_object_key,
    'evidence', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', id, 'excerpt', excerpt, 'locator', locator_json,
        'sourceHash', source_hash
      ) order by (locator_json ->> 'page_number')::integer)
      from public.asset_evidence
      where revision_id = v_revision.id and user_id = v_job.user_id and locator_type = 'pdf'
    ), '[]'::jsonb)
  );
end;
$$;

revoke all on function public.get_source_note_context(uuid,text,bigint) from public;
grant execute on function public.get_source_note_context(uuid,text,bigint) to service_role;

create or replace function public.publish_source_note(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_model_provider text,
  p_model_name text,
  p_prompt_version text,
  p_output_hash text,
  p_title text,
  p_body text,
  p_claims jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_revision public.asset_revisions%rowtype;
  v_note_id uuid;
  v_claim jsonb;
  v_citation jsonb;
  v_claim_id uuid;
  v_evidence public.asset_evidence%rowtype;
  v_claim_type text;
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'generate_source_note' then return null; end if;
  if v_job.status = 'completed' then
    select note_id into v_note_id from public.source_note_generations where job_id = p_job_id;
    return case when v_note_id is null then null else
      jsonb_build_object('jobId', p_job_id, 'noteId', v_note_id, 'idempotent', true) end;
  end if;
  if v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then return null; end if;
  if p_model_provider is null or char_length(p_model_provider) not between 1 and 64
     or p_model_name is null or char_length(p_model_name) not between 1 and 128
     or p_prompt_version is null
     or p_prompt_version !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
     or p_output_hash is null or p_output_hash !~ '^[0-9a-f]{64}$'
     or p_title is null or char_length(p_title) not between 1 and 300
     or p_body is null or char_length(p_body) not between 1 and 100000
     or jsonb_typeof(p_claims) is distinct from 'array'
     or jsonb_array_length(p_claims) not between 1 and 100 then
    raise invalid_parameter_value using message = 'source note publication is invalid';
  end if;
  select * into v_revision from public.asset_revisions
  where id = (v_job.payload_json #>> '{input,revisionId}')::uuid
    and id = (select active_revision_id from public.assets where id = v_job.asset_id)
    and asset_id = v_job.asset_id and user_id = v_job.user_id and status = 'validated';
  if not found then return null; end if;

  -- Validate every citation before any Note row is created.
  for v_claim in select value from jsonb_array_elements(p_claims) loop
    if jsonb_typeof(v_claim) is distinct from 'object'
       or v_claim ->> 'text' is null
       or char_length(v_claim ->> 'text') not between 1 and 4000
       or v_claim ->> 'category' is null
       or v_claim ->> 'category' not in ('fact','data','argument','conclusion','uncertainty')
       or jsonb_typeof(v_claim -> 'citations') is distinct from 'array'
       or jsonb_array_length(v_claim -> 'citations') not between 1 and 20 then
      raise invalid_parameter_value using message = 'claim payload is invalid';
    end if;
    for v_citation in select value from jsonb_array_elements(v_claim -> 'citations') loop
      if v_citation ->> 'evidenceId' is null
         or (v_citation ->> 'evidenceId') !~
           '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
         or v_citation ->> 'quote' is null
         or char_length(v_citation ->> 'quote') not between 1 and 4000 then
        raise invalid_parameter_value using message = 'citation payload is invalid';
      end if;
      select * into v_evidence from public.asset_evidence
      where id = (v_citation ->> 'evidenceId')::uuid
        and revision_id = v_revision.id and user_id = v_job.user_id;
      if not found or position((v_citation ->> 'quote') in v_evidence.excerpt) = 0 then
        raise integrity_constraint_violation using message = 'citation is not supported by source evidence';
      end if;
    end loop;
  end loop;

  insert into public.notes (user_id, note_type, title, body, publication_state)
  values (v_job.user_id, 'source_note', p_title, p_body, 'draft') returning id into v_note_id;
  insert into public.note_assets (user_id, note_id, asset_id, relationship)
  values (v_job.user_id, v_note_id, v_job.asset_id, 'derived_from');
  insert into public.source_note_generations (
    user_id, note_id, asset_id, source_revision_id, asset_id_snapshot,
    source_revision_id_snapshot, source_fingerprint, job_id,
    model_provider, model_name, prompt_version, output_hash
  ) values (
    v_job.user_id, v_note_id, v_job.asset_id, v_revision.id, v_job.asset_id,
    v_revision.id, v_revision.fingerprint, v_job.id,
    p_model_provider, p_model_name, p_prompt_version, p_output_hash
  );

  for v_claim in select value from jsonb_array_elements(p_claims) loop
    v_claim_type := case v_claim ->> 'category'
      when 'fact' then 'source_assertion'
      when 'data' then 'source_assertion'
      when 'uncertainty' then 'hypothesis'
      else 'inference' end;
    insert into public.claims (
      user_id, note_id, text, claim_type, verification_status,
      needs_review, source_note_revision
    ) values (
      v_job.user_id, v_note_id, v_claim ->> 'text', v_claim_type,
      'unreviewed', true, 1
    ) returning id into v_claim_id;
    for v_citation in select value from jsonb_array_elements(v_claim -> 'citations') loop
      select * into strict v_evidence from public.asset_evidence
      where id = (v_citation ->> 'evidenceId')::uuid
        and revision_id = v_revision.id and user_id = v_job.user_id;
      insert into public.claim_evidence (
        user_id, claim_id, evidence_id, evidence_id_snapshot,
        source_revision_id, source_revision_id_snapshot, relation_type,
        validity_status, locator_json, supporting_quote
      ) values (
        v_job.user_id, v_claim_id, v_evidence.id, v_evidence.id,
        v_revision.id, v_revision.id, 'asserts', 'valid',
        v_evidence.locator_json, v_citation ->> 'quote'
      );
    end loop;
  end loop;

  update public.job_attempts set ended_at = now(), outcome = 'completed'
  where job_id = v_job.id and lease_generation = p_lease_generation and ended_at is null;
  update public.jobs set status = 'completed', lease_owner = null,
    lease_expires_at = null, last_error_class = null,
    last_error_code = null, last_error_message = null
  where id = v_job.id;
  return jsonb_build_object('jobId', v_job.id, 'noteId', v_note_id, 'idempotent', false);
end;
$$;

revoke all on function public.publish_source_note(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) from public;
grant execute on function public.publish_source_note(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) to service_role;
