-- Deep Research Phase 5: run-scoped Web Evidence, source lifecycle, verified
-- result persistence, and Web citation snapshots. Legacy publication is unchanged.

create table public.deep_web_sources (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  canonical_url text not null check (
    canonical_url ~ '^https://[^/@:]+(?::443)?/' and char_length(canonical_url) <= 2000
  ),
  source_title text not null check (char_length(source_title) between 1 and 500),
  latest_content_hash text not null check (latest_content_hash ~ '^[0-9a-f]{64}$'),
  status text not null check (status in ('valid','deleted','unavailable')),
  first_captured_at timestamptz not null default now(),
  last_checked_at timestamptz not null default now(),
  unique (user_id, canonical_url),
  unique (id, user_id)
);

create table public.deep_web_evidence (
  id uuid primary key,
  user_id uuid not null,
  research_run_id uuid not null,
  source_id uuid not null,
  canonical_url text not null,
  source_title text not null check (char_length(source_title) between 1 and 500),
  provider text not null check (char_length(provider) between 1 and 64),
  excerpt text not null check (char_length(excerpt) between 1 and 12000),
  locator_json jsonb not null check (jsonb_typeof(locator_json) = 'object'),
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  source_published_at timestamptz,
  captured_at timestamptz not null,
  citation_eligible boolean not null,
  is_search_snippet boolean not null default false,
  validity_status text not null check (
    validity_status in ('valid','invalid','source_deleted','superseded')
  ),
  created_at timestamptz not null default now(),
  constraint deep_web_evidence_run_owner_fk foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint deep_web_evidence_source_owner_fk foreign key (source_id, user_id)
    references public.deep_web_sources (id, user_id) on delete cascade,
  constraint deep_web_evidence_snippet_check check (
    not is_search_snippet or not citation_eligible
  ),
  unique (research_run_id, canonical_url, content_hash),
  unique (research_run_id, id)
);

create index deep_web_evidence_user_run_idx
  on public.deep_web_evidence (user_id, research_run_id, created_at);

create table public.deep_research_results (
  research_run_id uuid primary key,
  user_id uuid not null,
  result_status text not null check (
    result_status in ('verified_complete','verified_partial','insufficient')
  ),
  stop_reason text not null,
  title text check (title is null or char_length(title) between 1 and 300),
  body text check (body is null or char_length(body) between 1 and 100000),
  findings_json jsonb not null default '[]'::jsonb check (jsonb_typeof(findings_json) = 'array'),
  verification_json jsonb not null default '{}'::jsonb check (jsonb_typeof(verification_json) = 'object'),
  output_hash text check (output_hash is null or output_hash ~ '^[0-9a-f]{64}$'),
  published_note_id uuid,
  updated_at timestamptz not null default now(),
  constraint deep_research_results_run_owner_fk foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint deep_research_results_note_owner_fk foreign key (published_note_id, user_id)
    references public.notes (id, user_id) on delete set null (published_note_id)
);

create table public.deep_web_citations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  research_run_id uuid not null,
  note_id uuid not null,
  finding_index integer not null check (finding_index between 0 and 99),
  citation_index integer not null check (citation_index between 0 and 19),
  relation_type text not null check (relation_type in ('supports','contradicts','contextualizes')),
  web_evidence_id uuid references public.deep_web_evidence (id) on delete set null,
  web_evidence_id_snapshot uuid not null,
  canonical_url text not null,
  source_title text not null,
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  locator_json jsonb not null check (jsonb_typeof(locator_json) = 'object'),
  supporting_quote text not null check (char_length(supporting_quote) between 1 and 4000),
  created_at timestamptz not null default now(),
  constraint deep_web_citations_run_owner_fk foreign key (research_run_id, user_id)
    references public.research_runs (id, user_id) on delete cascade,
  constraint deep_web_citations_note_owner_fk foreign key (note_id, user_id)
    references public.notes (id, user_id) on delete cascade,
  unique (research_run_id, finding_index, citation_index)
);

alter table public.deep_web_sources enable row level security;
alter table public.deep_web_sources force row level security;
alter table public.deep_web_evidence enable row level security;
alter table public.deep_web_evidence force row level security;
alter table public.deep_research_results enable row level security;
alter table public.deep_research_results force row level security;
alter table public.deep_web_citations enable row level security;
alter table public.deep_web_citations force row level security;

create policy deep_web_sources_select_own on public.deep_web_sources
for select to authenticated using ((select auth.uid()) = user_id);
create policy deep_web_evidence_select_own on public.deep_web_evidence
for select to authenticated using ((select auth.uid()) = user_id);
create policy deep_research_results_select_own on public.deep_research_results
for select to authenticated using ((select auth.uid()) = user_id);
create policy deep_web_citations_select_own on public.deep_web_citations
for select to authenticated using ((select auth.uid()) = user_id);

revoke all on public.deep_web_sources, public.deep_web_evidence,
  public.deep_research_results, public.deep_web_citations from anon, authenticated;
grant select on public.deep_web_sources, public.deep_web_evidence,
  public.deep_research_results, public.deep_web_citations to authenticated;
grant select, insert, update, delete on public.deep_web_sources,
  public.deep_web_evidence, public.deep_research_results, public.deep_web_citations
  to service_role;

create or replace function public.append_deep_web_evidence(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_operation_id uuid,
  p_evidence jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_operation public.research_operations%rowtype;
  v_row jsonb;
  v_source public.deep_web_sources%rowtype;
  v_count integer := 0;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  select * into v_operation from public.research_operations
  where id = p_operation_id and research_run_id = v_run.id
    and lease_generation = p_lease_generation and status = 'running';
  if not found then return null; end if;
  if v_run.web_policy <> 'auto'
     or v_run.request_json #>> '{permissions,webDiscovery}' <> 'true'
     or v_run.request_json #>> '{permissions,webFetch}' <> 'true'
     or jsonb_typeof(p_evidence) <> 'array'
     or jsonb_array_length(p_evidence) not between 1 and 8 then
    raise insufficient_privilege using message = 'Web Evidence capture is not authorized';
  end if;
  for v_row in select value from jsonb_array_elements(p_evidence) loop
    if v_row ->> 'schemaVersion' <> 'evidence-record-v1'
       or v_row ->> 'origin' <> 'web'
       or v_row ->> 'runId' <> v_run.id::text
       or v_row ->> 'userId' <> v_run.user_id::text
       or v_row ->> 'storageScope' <> 'research_run'
       or (v_row ->> 'citationEligible')::boolean is not true
       or (v_row ->> 'isSearchSnippet')::boolean is true
       or v_row ->> 'validityStatus' <> 'valid'
       or v_row ->> 'sourceIdentity' !~ '^https://[^/@:]+(?::443)?/'
       or v_row ->> 'contentHash' !~ '^[0-9a-f]{64}$'
       or char_length(v_row ->> 'excerpt') not between 1 and 12000
       or jsonb_typeof(v_row -> 'citationLocator') <> 'object' then
      raise invalid_parameter_value using message = 'Web Evidence is invalid';
    end if;
    insert into public.deep_web_sources (
      user_id, canonical_url, source_title, latest_content_hash, status
    ) values (
      v_run.user_id, v_row ->> 'sourceIdentity',
      coalesce(v_row #>> '{sourceMetadata,title}', v_row ->> 'sourceIdentity'),
      v_row ->> 'contentHash', 'valid'
    ) on conflict (user_id, canonical_url) do update set
      source_title = excluded.source_title,
      latest_content_hash = excluded.latest_content_hash,
      status = 'valid', last_checked_at = now()
    returning * into v_source;
    update public.deep_web_evidence set validity_status = 'superseded'
    where user_id = v_run.user_id and source_id = v_source.id
      and content_hash <> (v_row ->> 'contentHash') and validity_status = 'valid';
    insert into public.deep_web_evidence (
      id, user_id, research_run_id, source_id, canonical_url, source_title,
      provider, excerpt, locator_json, content_hash, source_published_at,
      captured_at, citation_eligible, is_search_snippet, validity_status
    ) values (
      (v_row ->> 'evidenceId')::uuid, v_run.user_id, v_run.id, v_source.id,
      v_row ->> 'sourceIdentity', v_source.source_title,
      coalesce(v_row #>> '{sourceMetadata,provider}', 'unknown'),
      v_row ->> 'excerpt', v_row -> 'citationLocator', v_row ->> 'contentHash',
      nullif(v_row ->> 'sourcePublishedAt', '')::timestamptz,
      (v_row ->> 'capturedAt')::timestamptz, true, false, 'valid'
    ) on conflict (research_run_id, canonical_url, content_hash) do nothing;
    v_count := v_count + 1;
  end loop;
  return jsonb_build_object('researchRunId', v_run.id, 'processedEvidence', v_count);
end;
$$;

create or replace function public.mark_deep_web_source_status(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_canonical_url text,
  p_status text
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_source_id uuid;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return false; end if;
  if p_status not in ('deleted','unavailable') then
    raise invalid_parameter_value using message = 'Web source status is invalid';
  end if;
  update public.deep_web_sources set status = p_status, last_checked_at = now()
  where user_id = v_run.user_id and canonical_url = p_canonical_url
  returning id into v_source_id;
  if not found then return false; end if;
  update public.deep_web_evidence set
    validity_status = case when p_status = 'deleted' then 'source_deleted' else 'invalid' end,
    citation_eligible = false
  where user_id = v_run.user_id and source_id = v_source_id;
  return true;
end;
$$;

create or replace function public.record_deep_research_result(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_result_status text,
  p_stop_reason text,
  p_title text,
  p_body text,
  p_findings jsonb,
  p_verification jsonb,
  p_output_hash text
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  if p_result_status not in ('verified_complete','verified_partial','insufficient')
     or jsonb_typeof(p_findings) <> 'array'
     or jsonb_typeof(p_verification) <> 'object'
     or (p_result_status like 'verified_%' and jsonb_array_length(p_findings) = 0)
     or (p_output_hash is not null and p_output_hash !~ '^[0-9a-f]{64}$') then
    raise invalid_parameter_value using message = 'Deep Research result is invalid';
  end if;
  insert into public.deep_research_results (
    research_run_id, user_id, result_status, stop_reason, title, body,
    findings_json, verification_json, output_hash
  ) values (
    v_run.id, v_run.user_id, p_result_status, p_stop_reason, p_title, p_body,
    p_findings, p_verification, p_output_hash
  ) on conflict (research_run_id) do update set
    result_status = excluded.result_status, stop_reason = excluded.stop_reason,
    title = excluded.title, body = excluded.body,
    findings_json = excluded.findings_json,
    verification_json = excluded.verification_json,
    output_hash = excluded.output_hash, updated_at = now();
  return jsonb_build_object('researchRunId', v_run.id, 'resultStatus', p_result_status);
end;
$$;

revoke all on function public.append_deep_web_evidence(uuid,text,bigint,uuid,jsonb)
  from public, anon, authenticated, service_role;
revoke all on function public.mark_deep_web_source_status(uuid,text,bigint,text,text)
  from public, anon, authenticated, service_role;
revoke all on function public.record_deep_research_result(
  uuid,text,bigint,text,text,text,text,jsonb,jsonb,text
) from public, anon, authenticated, service_role;
grant execute on function public.append_deep_web_evidence(uuid,text,bigint,uuid,jsonb)
  to service_role;
grant execute on function public.mark_deep_web_source_status(uuid,text,bigint,text,text)
  to service_role;
grant execute on function public.record_deep_research_result(
  uuid,text,bigint,text,text,text,text,jsonb,jsonb,text
) to service_role;

create or replace function public.load_deep_research_state(
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
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_plan jsonb;
  v_state jsonb;
  v_assessment jsonb;
  v_budget jsonb;
  v_evidence jsonb;
  v_operations jsonb;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  select plan_json into v_plan from public.research_plan_versions
  where research_run_id = v_run.id order by plan_version desc limit 1;
  select state_json into v_state from public.research_checkpoints
  where research_run_id = v_run.id order by checkpoint_version desc limit 1;
  select assessment_json into v_assessment from public.research_assessments
  where research_run_id = v_run.id order by created_at desc limit 1;
  select jsonb_build_object(
    'limitUsd', limit_usd, 'protectedFinalReportReserveUsd', protected_final_reserve_usd,
    'reservedUsd', reserved_usd, 'actualUsd', actual_usd, 'uncertainUsd', uncertain_usd
  ) into v_budget from public.research_run_budgets where research_run_id = v_run.id;
  select coalesce(jsonb_agg(item order by sort_order, evidence_id), '[]'::jsonb)
  into v_evidence from (
    select 0 sort_order, rre.evidence_id, jsonb_build_object(
      'id', rre.evidence_id, 'assetId', rre.asset_id, 'revisionId', rre.revision_id,
      'excerpt', ae.excerpt, 'locator', ae.locator_json, 'sourceHash', ae.source_hash,
      'retrievalMethod', rre.retrieval_method, 'retrievalScore', rre.retrieval_score,
      'queryHits', rre.query_hits
    ) item
    from public.research_run_evidence rre
    join public.asset_evidence ae on ae.id = rre.evidence_id and ae.user_id = v_run.user_id
    where rre.research_run_id = v_run.id
    union all
    select 1, dwe.id, jsonb_strip_nulls(jsonb_build_object(
      'schemaVersion', 'evidence-record-v1', 'evidenceId', dwe.id,
      'runId', dwe.research_run_id, 'userId', dwe.user_id, 'origin', 'web',
      'sourceIdentity', dwe.canonical_url,
      'sourceMetadata', jsonb_build_object(
        'title', dwe.source_title, 'provider', dwe.provider, 'url', dwe.canonical_url
      ),
      'provenance', jsonb_build_object(
        'assetId', null, 'revisionId', null, 'parentEvidenceIds', '[]'::jsonb,
        'captureMethod', 'web_fetch'
      ),
      'excerpt', dwe.excerpt, 'citationLocator', dwe.locator_json,
      'contentHash', dwe.content_hash, 'capturedAt', dwe.captured_at,
      'sourcePublishedAt', dwe.source_published_at, 'storageScope', 'research_run',
      'citationEligible', dwe.citation_eligible,
      'validityStatus', dwe.validity_status, 'isSearchSnippet', dwe.is_search_snippet
    ))
    from public.deep_web_evidence dwe where dwe.research_run_id = v_run.id
  ) all_evidence;
  select coalesce(jsonb_agg(jsonb_build_object(
    'operationId', id, 'idempotencyKey', idempotency_key,
    'actionType', action_type, 'status', status, 'estimatedCostUsd', estimated_cost_usd,
    'actualCostUsd', actual_cost_usd, 'uncertainCostUsd', uncertain_cost_usd,
    'retryCount', retry_count
  ) order by created_at), '[]'::jsonb) into v_operations
  from public.research_operations where research_run_id = v_run.id;
  return jsonb_build_object(
    'request', v_run.request_json, 'plan', v_plan, 'state', v_state,
    'assessment', v_assessment, 'budget', v_budget,
    'evidence', v_evidence, 'operations', v_operations
  );
end;
$$;

create or replace function public.publish_deep_research_draft(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_model_provider text,
  p_model_name text,
  p_prompt_version text,
  p_output_hash text,
  p_title text,
  p_body text,
  p_findings jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_context record;
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_note_id uuid;
  v_finding jsonb;
  v_citation jsonb;
  v_internal public.asset_evidence%rowtype;
  v_web public.deep_web_evidence%rowtype;
  v_asset_id uuid;
  v_finding_index integer := 0;
  v_citation_index integer;
begin
  select * into v_context
  from public.deep_research_job_context(p_job_id, p_worker_id, p_lease_generation);
  v_job := v_context.job_row;
  v_run := v_context.run_row;
  if v_job.id is null then return null; end if;
  if p_model_provider is null or char_length(p_model_provider) not between 1 and 64
     or p_model_name is null or char_length(p_model_name) not between 1 and 128
     or p_prompt_version !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
     or p_output_hash !~ '^[0-9a-f]{64}$'
     or char_length(p_title) not between 1 and 300
     or char_length(p_body) not between 1 and 100000
     or jsonb_typeof(p_findings) <> 'array'
     or jsonb_array_length(p_findings) not between 1 and 100 then
    raise invalid_parameter_value using message = 'Deep Research draft publication is invalid';
  end if;
  for v_finding in select value from jsonb_array_elements(p_findings) loop
    if char_length(v_finding ->> 'text') not between 1 and 8000
       or char_length(v_finding ->> 'uncertainty') not between 1 and 2000
       or jsonb_typeof(v_finding -> 'citations') <> 'array'
       or jsonb_array_length(v_finding -> 'citations') not between 1 and 20 then
      raise invalid_parameter_value using message = 'Deep Research finding is invalid';
    end if;
    for v_citation in select value from jsonb_array_elements(v_finding -> 'citations') loop
      v_internal := null;
      v_web := null;
      select ae.* into v_internal
      from public.research_run_evidence rre
      join public.asset_evidence ae on ae.id = rre.evidence_id and ae.user_id = rre.user_id
      join public.asset_revisions ar on ar.id = ae.revision_id and ar.status = 'validated'
      join public.assets a on a.id = ar.asset_id and a.active_revision_id = ar.id and a.status = 'ready'
      where rre.research_run_id = v_run.id
        and rre.evidence_id = (v_citation ->> 'evidenceId')::uuid
        and position((v_citation ->> 'quote') in ae.excerpt) > 0;
      if not found then
        select * into v_web from public.deep_web_evidence
        where research_run_id = v_run.id
          and id = (v_citation ->> 'evidenceId')::uuid
          and citation_eligible and not is_search_snippet and validity_status = 'valid'
          and position((v_citation ->> 'quote') in excerpt) > 0;
        if not found then
          raise integrity_constraint_violation
            using message = 'Deep Research citation is not supported by current Evidence';
        end if;
      end if;
    end loop;
  end loop;
  insert into public.notes (user_id, note_type, title, body, publication_state)
  values (v_run.user_id, 'research_note', p_title, p_body, 'draft')
  returning id into v_note_id;
  for v_finding in select value from jsonb_array_elements(p_findings) loop
    v_citation_index := 0;
    for v_citation in select value from jsonb_array_elements(v_finding -> 'citations') loop
      select ae.* into v_internal
      from public.research_run_evidence rre
      join public.asset_evidence ae on ae.id = rre.evidence_id and ae.user_id = rre.user_id
      where rre.research_run_id = v_run.id
        and rre.evidence_id = (v_citation ->> 'evidenceId')::uuid;
      if found then
        select asset_id into strict v_asset_id from public.asset_revisions
        where id = v_internal.revision_id and user_id = v_run.user_id;
        insert into public.research_citations (
          user_id, research_run_id, note_id, finding_index, citation_index,
          relation_type, evidence_id, evidence_id_snapshot, asset_id, asset_id_snapshot,
          source_revision_id, source_revision_id_snapshot, locator_json, supporting_quote
        ) values (
          v_run.user_id, v_run.id, v_note_id, v_finding_index, v_citation_index,
          v_citation ->> 'relationType', v_internal.id, v_internal.id, v_asset_id, v_asset_id,
          v_internal.revision_id, v_internal.revision_id, v_internal.locator_json,
          v_citation ->> 'quote'
        );
        insert into public.note_assets (user_id, note_id, asset_id, relationship)
        values (v_run.user_id, v_note_id, v_asset_id, 'context')
        on conflict (note_id, asset_id) do nothing;
      else
        select * into strict v_web from public.deep_web_evidence
        where research_run_id = v_run.id
          and id = (v_citation ->> 'evidenceId')::uuid;
        insert into public.deep_web_citations (
          user_id, research_run_id, note_id, finding_index, citation_index,
          relation_type, web_evidence_id, web_evidence_id_snapshot,
          canonical_url, source_title, content_hash, locator_json, supporting_quote
        ) values (
          v_run.user_id, v_run.id, v_note_id, v_finding_index, v_citation_index,
          v_citation ->> 'relationType', v_web.id, v_web.id, v_web.canonical_url,
          v_web.source_title, v_web.content_hash, v_web.locator_json,
          v_citation ->> 'quote'
        );
      end if;
      v_citation_index := v_citation_index + 1;
    end loop;
    v_finding_index := v_finding_index + 1;
  end loop;
  update public.research_runs set
    status = 'draft_ready', output_note_id = v_note_id,
    model_provider = p_model_provider, model_name = p_model_name,
    prompt_version = p_prompt_version, output_hash = p_output_hash
  where id = v_run.id;
  insert into public.deep_research_results (
    research_run_id, user_id, result_status, stop_reason, title, body,
    findings_json, verification_json, output_hash, published_note_id
  ) values (
    v_run.id, v_run.user_id, 'verified_complete', 'quality_reached', p_title, p_body,
    p_findings, '{"gate":"passed"}'::jsonb, p_output_hash, v_note_id
  ) on conflict (research_run_id) do update set
    result_status = 'verified_complete', title = excluded.title, body = excluded.body,
    findings_json = excluded.findings_json, verification_json = excluded.verification_json,
    output_hash = excluded.output_hash, published_note_id = excluded.published_note_id,
    updated_at = now();
  update public.job_attempts set ended_at = now(), outcome = 'completed'
  where job_id = v_job.id and lease_generation = p_lease_generation and ended_at is null;
  update public.jobs set status = 'completed', lease_owner = null,
    lease_expires_at = null, last_error_class = null,
    last_error_code = null, last_error_message = null where id = v_job.id;
  return jsonb_build_object(
    'jobId', v_job.id, 'researchRunId', v_run.id,
    'noteId', v_note_id, 'idempotent', false
  );
end;
$$;

revoke all on function public.publish_deep_research_draft(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) from public, anon, authenticated, service_role;
grant execute on function public.publish_deep_research_draft(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) to service_role;
