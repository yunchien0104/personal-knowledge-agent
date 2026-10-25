create or replace function public.get_research_context(
  p_job_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_plan jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_job public.jobs%rowtype;
  v_run public.research_runs%rowtype;
  v_max_queries integer;
  v_evidence jsonb;
begin
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found or v_job.job_type <> 'execute_research'
     or v_job.status <> 'running' or v_job.lease_owner <> p_worker_id
     or v_job.lease_generation <> p_lease_generation
     or v_job.lease_expires_at <= now() then
    return null;
  end if;
  select * into v_run from public.research_runs
  where id = (v_job.payload_json #>> '{input,researchRunId}')::uuid
    and user_id = v_job.user_id and job_id = v_job.id
  for update;
  if not found then return null; end if;

  if jsonb_typeof(p_plan) is distinct from 'object'
     or p_plan ->> 'question' <> v_run.question
     or jsonb_typeof(p_plan -> 'subquestions') is distinct from 'array'
     or jsonb_array_length(p_plan -> 'subquestions') not between 1 and 5
     or jsonb_typeof(p_plan -> 'evidenceRequirements') is distinct from 'array'
     or jsonb_array_length(p_plan -> 'evidenceRequirements') not between 1 and 10
     or jsonb_typeof(p_plan -> 'stopConditions') is distinct from 'array'
     or jsonb_array_length(p_plan -> 'stopConditions') not between 1 and 10
     or jsonb_typeof(p_plan -> 'maxCostUsd') is distinct from 'number'
     or (p_plan ->> 'maxCostUsd')::numeric <> 0
     or jsonb_typeof(p_plan -> 'maxQueries') is distinct from 'number'
     or (p_plan ->> 'maxQueries')::integer not between 1 and 3
     or jsonb_typeof(p_plan -> 'maxRounds') is distinct from 'number'
     or (p_plan ->> 'maxRounds')::integer <> 1 then
    raise invalid_parameter_value using message = 'research plan is invalid';
  end if;
  v_max_queries := (p_plan ->> 'maxQueries')::integer;


  -- Normalize PDF line breaks and score overlapping Chinese bigrams.
  with query_terms as (
    select regexp_replace(value #>> '{}', '[[:space:]]+', '', 'g') as query_text
    from jsonb_array_elements(p_plan -> 'subquestions') with ordinality
    where jsonb_typeof(value) = 'string'
      and char_length(btrim(value #>> '{}')) between 2 and 200
    order by ordinality
    limit v_max_queries
  ),
  query_grams as (
    select distinct substr(q.query_text, g.i, 2) as gram
    from query_terms q
    cross join lateral generate_series(1, greatest(length(q.query_text) - 1, 0)) g(i)
    where substr(q.query_text, g.i, 2) ~ '[一-龥]'
  ),
  candidates as (
    select e.id, r.asset_id, e.revision_id,
           e.excerpt, e.locator_json,
           regexp_replace(e.excerpt, '[[:space:]]+', '', 'g') as normalized
    from public.asset_evidence e
    join public.asset_revisions r
      on r.id = e.revision_id
      and r.user_id = e.user_id
      and r.status = 'validated'
    join public.assets a
      on a.id = r.asset_id
      and a.user_id = r.user_id
      and a.active_revision_id = r.id
      and a.status = 'ready'
    where e.user_id = v_job.user_id
  ),
  ranked as (
    select c.id, c.asset_id, c.revision_id,
           c.excerpt, c.locator_json,
           count(distinct q.gram)::double precision as relevance
    from candidates c
    join query_grams q
      on strpos(c.normalized, q.gram) > 0
    group by c.id, c.asset_id, c.revision_id,
             c.excerpt, c.locator_json
    having count(distinct q.gram) >= 2
    order by relevance desc, c.id
    limit 20
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', id,
    'assetId', asset_id,
    'revisionId', revision_id,
    'excerpt', excerpt,
    'locator', locator_json,
    'relevance', relevance
  ) order by relevance desc, id), '[]'::jsonb)
  into v_evidence
  from ranked;

  update public.research_runs set plan_json = p_plan, status = 'running'
  where id = v_run.id;
  return jsonb_build_object(
    'researchRunId', v_run.id,
    'question', v_run.question,
    'plan', p_plan,
    'evidence', v_evidence
  );
end;
$$;
