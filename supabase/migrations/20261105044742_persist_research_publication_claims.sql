create or replace function public.publish_internal_deep_research_draft(
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
  v_result jsonb;
  v_note_id uuid;
  v_run_id uuid;
  v_user_id uuid;
  v_note_revision integer;
  v_existing_claims integer;
  v_expected_claims integer;
  v_finding jsonb;
  v_finding_index integer;
  v_citation_index integer;
  v_claim_id uuid;
  v_claim_type text;
  v_citation public.research_citations%rowtype;
begin
  -- Reuse the existing fenced, citation-validating, idempotent publication RPC.
  -- Because this wrapper runs in the same transaction, the Note, provenance,
  -- Claims, and claim-to-Evidence rows commit or roll back together.
  v_result := public.publish_research_draft(
    p_job_id, p_worker_id, p_lease_generation, p_model_provider, p_model_name,
    p_prompt_version, p_output_hash, p_title, p_body, p_findings
  );
  if v_result is null then
    return null;
  end if;

  v_note_id := (v_result ->> 'noteId')::uuid;
  v_run_id := (v_result ->> 'researchRunId')::uuid;
  select rr.user_id, n.body_revision
    into strict v_user_id, v_note_revision
  from public.research_runs rr
  join public.notes n on n.id = rr.output_note_id and n.user_id = rr.user_id
  where rr.id = v_run_id and rr.output_note_id = v_note_id;

  v_expected_claims := jsonb_array_length(p_findings);
  select count(*) into v_existing_claims
  from public.claims where note_id = v_note_id and user_id = v_user_id;
  if v_existing_claims = v_expected_claims then
    return v_result;
  end if;
  if v_existing_claims <> 0 then
    raise integrity_constraint_violation
      using message = 'research publication claim persistence is incomplete';
  end if;

  for v_finding, v_finding_index in
    select value, (ordinality - 1)::integer
    from jsonb_array_elements(p_findings) with ordinality
  loop
    v_claim_type := case v_finding ->> 'uncertainty'
      when 'Evidence-based inference.' then 'inference'
      when 'Evidence-supported hypothesis; not a proven fact.' then 'hypothesis'
      else 'source_assertion'
    end;
    insert into public.claims (
      user_id, note_id, text, claim_type, verification_status,
      needs_review, source_note_revision
    ) values (
      v_user_id, v_note_id, v_finding ->> 'text', v_claim_type,
      'corroborated', false, v_note_revision
    ) returning id into v_claim_id;

    for v_citation_index in
      select (ordinality - 1)::integer
      from jsonb_array_elements(v_finding -> 'citations') with ordinality
    loop
      select * into strict v_citation
      from public.research_citations
      where research_run_id = v_run_id and note_id = v_note_id
        and finding_index = v_finding_index
        and citation_index = v_citation_index;
      insert into public.claim_evidence (
        user_id, claim_id, evidence_id, evidence_id_snapshot,
        source_revision_id, source_revision_id_snapshot, relation_type,
        validity_status, locator_json, supporting_quote
      ) values (
        v_user_id, v_claim_id, v_citation.evidence_id,
        v_citation.evidence_id_snapshot, v_citation.source_revision_id,
        v_citation.source_revision_id_snapshot, v_citation.relation_type,
        'valid', v_citation.locator_json, v_citation.supporting_quote
      );
    end loop;
  end loop;
  return v_result;
end;
$$;

revoke all on function public.publish_internal_deep_research_draft(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) from public, anon, authenticated, service_role;
grant execute on function public.publish_internal_deep_research_draft(
  uuid,text,bigint,text,text,text,text,text,text,jsonb
) to service_role;
