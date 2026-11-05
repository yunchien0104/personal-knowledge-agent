"""Controlled Local-Supabase replay of a durable Deep finalization checkpoint.

This entry point is intentionally excluded from pytest. It never mutates the
terminal source run. ``--preflight-only`` performs read-only reconstruction and
payload/budget checks. The execution path creates exactly one new Local-only
run/job that reuses immutable plan, assessment, and Evidence records, then runs
only R2A -> R2E -> R2F -> result persistence/publication.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import UUID, uuid4

from personal_brain.retrieval_gateway import RetrievalSupabaseGateway
from personal_brain.research.deep_contracts import (
    EvidenceAssessmentV1,
    ResearchIterationStateV1,
    ResearchPlanV1,
    ResearchRequestV1,
)
from personal_brain.research.deep_coordinator import (
    DeepResearchCoordinator,
    _evidence_records,
    _publication_rows,
)
from personal_brain.research.deep_finalization import DeepFinalizationEngine
from personal_brain.research.llm import OpenAICompatibleResearchModel
from personal_brain.research.orchestrator import ResearchOrchestrator
from personal_brain.research.verifier import LLMClaimJudge
from personal_brain.supabase_gateway import GatewayError


SOURCE_RUN_DEFAULT = UUID("e7789eba-5d8e-454e-ac6e-252a65077e4f")
REQUIRED_MODEL = "gpt-6.1-sol"
REQUIRED_API_HOST = "api.openai.com"


class ForbiddenEarlierStage:
    """Fail closed if replay accidentally enters R2B, R1, R2C, or R2D."""

    def __init__(self, stage: str) -> None:
        self.stage = stage

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(
            f"Finalization replay attempted forbidden {self.stage}.{name}"
        )


def _load_provider_environment() -> None:
    """Load only final-provider values; never import hosted Supabase settings."""

    env_path = Path(__file__).resolve().parents[2] / ".env"
    allowed = {"LLM_PROVIDER", "LLM_API_BASE_URL", "LLM_API_KEY", "RESEARCH_MODEL"}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in allowed and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _assert_local_policy(*, paid: bool) -> None:
    db_url = urlparse(os.environ.get("LOCAL_REPLAY_DB_URL", ""))
    api_url = urlparse(os.environ.get("SUPABASE_SERVER_URL", ""))
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if db_url.scheme not in {"postgres", "postgresql"} or db_url.hostname not in local_hosts:
        raise RuntimeError("LOCAL_REPLAY_DB_URL must target loopback PostgreSQL")
    if api_url.scheme != "http" or api_url.hostname not in local_hosts:
        raise RuntimeError("SUPABASE_SERVER_URL must target loopback Supabase")
    if api_url.port != 60321 or db_url.port != 60322:
        raise RuntimeError("Replay is restricted to the retained 60321/60322 stack")
    if os.getenv("RESEARCH_MODEL") != REQUIRED_MODEL:
        raise RuntimeError("Finalization replay requires gpt-6.1-sol")
    if urlparse(os.getenv("LLM_API_BASE_URL", "")).hostname != REQUIRED_API_HOST:
        raise RuntimeError("Provider base URL does not match the approved OpenAI endpoint")
    if not os.getenv("LLM_API_KEY"):
        raise RuntimeError("Provider API key is not configured")
    if os.getenv("DEEP_FINAL_PRICING_MODEL") != REQUIRED_MODEL:
        raise RuntimeError("Verified pricing model does not match the provider model")
    if os.getenv("DEEP_FINAL_PRICING_VERIFIED", "").lower() != "true":
        raise RuntimeError("Finalization pricing is not verified")
    if os.getenv("DEEP_FINAL_SERVICE_TIER") != "default":
        raise RuntimeError("Only the approved Standard service tier is allowed")
    if os.getenv("DEEP_FINAL_REASONING_EFFORT") != "low":
        raise RuntimeError("Only the approved low reasoning effort is allowed")
    if int(os.getenv("DEEP_FINAL_MAX_CLAIMS", "0")) != 3:
        raise RuntimeError("This replay is restricted to at most three claims")
    if paid and os.getenv("FINALIZATION_REPLAY_REAL_PROVIDER_AUTHORIZED") != "true":
        raise RuntimeError("Explicit finalization replay authorization marker is missing")


def _psql(sql: str) -> str:
    completed = subprocess.run(
        [
            "psql",
            os.environ["LOCAL_REPLAY_DB_URL"],
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-Atq",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PGPASSWORD": os.getenv("LOCAL_REPLAY_DB_PASSWORD", "postgres")},
    )
    if completed.returncode:
        raise RuntimeError(f"Local PostgreSQL command failed: {completed.stderr[:1000]}")
    return completed.stdout.strip()


def _source_snapshot(source_run_id: UUID) -> dict[str, Any]:
    value = _psql(f"""
select jsonb_build_object(
  'runStatus', rr.status,
  'jobStatus', j.status,
  'userId', rr.user_id,
  'request', rr.request_json,
  'plan', p.plan_json,
  'assessment', a.assessment_json,
  'state', c.state_json,
  'evidence', coalesce((
    select jsonb_agg(jsonb_build_object(
      'id', rre.evidence_id, 'assetId', rre.asset_id,
      'revisionId', rre.revision_id, 'excerpt', ae.excerpt,
      'locator', ae.locator_json, 'sourceHash', ae.source_hash,
      'retrievalMethod', rre.retrieval_method,
      'retrievalScore', rre.retrieval_score, 'queryHits', rre.query_hits
    ) order by rre.retrieval_rank, rre.evidence_id)
    from public.research_run_evidence rre
    join public.asset_evidence ae on ae.id = rre.evidence_id
      and ae.user_id = rr.user_id
    where rre.research_run_id = rr.id
  ), '[]'::jsonb),
  'daily', coalesce((select to_jsonb(d) from public.research_daily_budgets d
    where d.user_id = rr.user_id
      and d.budget_day = (now() at time zone 'Asia/Taipei')::date), '{{}}'::jsonb),
  'monthly', coalesce((select to_jsonb(m) from public.research_budget_periods m
    where m.user_id = rr.user_id
      and m.period_start = date_trunc('month', now() at time zone 'Asia/Taipei')::date), '{{}}'::jsonb)
)::text
from public.research_runs rr
join public.jobs j on j.id = rr.job_id
join lateral (select plan_json from public.research_plan_versions
  where research_run_id=rr.id order by plan_version desc limit 1) p on true
join lateral (select assessment_json from public.research_assessments
  where research_run_id=rr.id order by created_at desc limit 1) a on true
join lateral (select state_json from public.research_checkpoints
  where research_run_id=rr.id order by checkpoint_version desc limit 1) c on true
where rr.id = '{source_run_id}';
""")
    if not value:
        raise RuntimeError("Source run reconstruction data is unavailable")
    result = json.loads(value)
    if result.get("runStatus") != "failed" or result.get("jobStatus") != "failed":
        raise RuntimeError("Source run/job must remain terminal failed")
    return result


def _build_finalizer() -> DeepFinalizationEngine:
    generator = OpenAICompatibleResearchModel()
    return DeepFinalizationEngine.from_legacy_orchestrator(
        ResearchOrchestrator(generator, LLMClaimJudge(generator.client))
    )


def _offline_preflight(
    snapshot: Mapping[str, Any], finalizer: DeepFinalizationEngine
) -> dict[str, Any]:
    request = ResearchRequestV1.model_validate(snapshot["request"])
    plan = ResearchPlanV1.model_validate(snapshot["plan"])
    assessment = EvidenceAssessmentV1.model_validate(snapshot["assessment"])
    state = ResearchIterationStateV1.model_validate(snapshot["state"])
    if state.executionPhase != "synthesizing" or state.targetOutcome not in {
        "complete", "qualified_partial"
    }:
        raise RuntimeError("Source checkpoint is not a legal finalization boundary")
    if plan.runId != request.runId or assessment.runId != request.runId:
        raise RuntimeError("Source plan/assessment identity is inconsistent")
    records = _evidence_records(list(snapshot["evidence"]), request)
    if len(records) != len(snapshot["evidence"]) or len(records) != 13:
        raise RuntimeError("Expected all 13 persisted internal Evidence records")
    publication_rows = _publication_rows(records)
    preflight = finalizer.preflight(
        request.runId,
        request.originalQuestion,
        plan.model_dump(mode="json"),
        publication_rows,
        assessment.model_dump(mode="json"),
    )
    manifest = preflight.payload_manifest
    included = set(manifest.get("includedEvidenceIds") or [])
    partial = {
        str(item.get("evidenceId"))
        for item in manifest.get("partiallyIncludedEvidence", [])
        if isinstance(item, Mapping) and item.get("evidenceId")
    }
    excluded = {
        str(item.get("evidenceId"))
        for item in manifest.get("excludedEvidence", [])
        if isinstance(item, Mapping) and item.get("evidenceId")
    }
    source_ids = {str(item.evidenceId) for item in records}
    if included | excluded != source_ids or included & excluded:
        raise RuntimeError("Payload Manifest does not account for every Evidence ID")
    shaped_ids = {str(row.get("id")) for row in preflight.synthesis_evidence}
    if shaped_ids != included:
        raise RuntimeError("Shaped R2A payload and Payload Manifest disagree")
    for row in preflight.synthesis_evidence:
        if not row.get("locator") or not row.get("assetId") or not row.get("revisionId"):
            raise RuntimeError("Citation provenance was lost during payload shaping")

    protected = finalizer.maximum_cost_usd()
    if protected > Decimal("1.0000") or preflight.maximum_cost_usd > protected:
        raise RuntimeError("Finalization envelope exceeds the authorized run cap")
    for name in ("daily", "monthly"):
        budget = snapshot.get(name) or {}
        limit = Decimal(str(budget.get("limit_usd", "0")))
        used = sum(
            Decimal(str(budget.get(key, "0")))
            for key in ("reserved_usd", "actual_usd", "uncertain_usd")
        )
        if used + protected > limit:
            raise RuntimeError(f"{name} budget cannot admit finalization worst case")
    return {
        "sourceRunId": str(request.runId),
        "checkpointVersion": state.checkpointVersion,
        "targetOutcome": state.targetOutcome,
        "evidenceCount": len(records),
        "includedEvidenceCount": len(included),
        "partiallyIncludedEvidenceCount": len(partial),
        "excludedEvidenceCount": len(excluded),
        "manifestHash": manifest["manifestHash"],
        "inputUpperBound": preflight.synthesis_input_upper_bound,
        "dynamicReservationUsd": format(preflight.maximum_cost_usd, "f"),
        "protectedWorstCaseUsd": format(protected, "f"),
        "dailyActualUsd": str((snapshot.get("daily") or {}).get("actual_usd", "0")),
        "monthlyActualUsd": str((snapshot.get("monthly") or {}).get("actual_usd", "0")),
    }


def _seed_replay(source_run_id: UUID, protected_usd: Decimal) -> UUID:
    new_run_id, new_job_id, new_request_id = uuid4(), uuid4(), uuid4()
    new_request_key, new_plan_id, new_assessment_id = uuid4(), uuid4(), uuid4()
    sql = f"""
begin;
do $$
begin
  if not exists (select 1 from public.research_runs rr join public.jobs j on j.id=rr.job_id
    where rr.id='{source_run_id}' and rr.status='failed' and j.status='failed') then
    raise exception 'source run is no longer terminal failed';
  end if;
  if exists (select 1 from public.jobs where resource_class='research'
    and status in ('queued','running','retry_wait')) then
    raise exception 'another research job is active';
  end if;
end $$;

create temp table replay_request on commit drop as
select (
  rr.request_json || jsonb_build_object(
    'runId','{new_run_id}', 'requestId','{new_request_id}',
    'idempotencyKey','{new_request_key}',
    'effectiveLimits', (rr.request_json->'effectiveLimits') || jsonb_build_object('maxCostUsd',1.0000)
  )
) request_json
from public.research_runs rr where rr.id='{source_run_id}';

insert into public.jobs (
  id,user_id,asset_id,job_type,payload_json,payload_schema_version,
  idempotency_key,resource_class,priority,status
)
select '{new_job_id}', rr.user_id, null, 'execute_research',
  jsonb_build_object(
    'schemaVersion',1,'jobId','{new_job_id}','userId',rr.user_id,
    'jobType','execute_research','idempotencyKey','deep-replay:{new_request_key}',
    'input',jsonb_build_object(
      'researchRunId','{new_run_id}','question',rr.question,
      'scopeAssetIds',rr.scope_asset_ids,'researchMode','deep',
      'researchRequest',rq.request_json
    )
  ), 1, 'deep-replay:{new_request_key}', 'research', 120, 'queued'
from public.research_runs rr cross join replay_request rq
where rr.id='{source_run_id}';

insert into public.research_runs (
  id,user_id,request_key,question,plan_json,status,job_id,scope_asset_ids,
  request_id,research_mode,web_policy,request_json,deadline_at,progress_json
)
select '{new_run_id}',rr.user_id,'{new_request_key}',rr.question,
  p.plan_json || jsonb_build_object(
    'runId','{new_run_id}','requestId','{new_request_id}','planId','{new_plan_id}'
  ),'queued','{new_job_id}',rr.scope_asset_ids,'{new_request_id}','deep','disabled',
  rq.request_json,now()+interval '30 minutes',
  jsonb_build_object('stage','synthesizing','checkpointVersion',1,'replaySourceRunId','{source_run_id}')
from public.research_runs rr
cross join replay_request rq
join lateral (select plan_json from public.research_plan_versions
  where research_run_id=rr.id order by plan_version desc limit 1) p on true
where rr.id='{source_run_id}';

insert into public.research_plan_versions (
  id,user_id,research_run_id,request_id,plan_version,schema_version,plan_json,
  parent_plan_id,revision_reason,quality_gate,model_provider,model_name,prompt_version
)
select '{new_plan_id}',p.user_id,'{new_run_id}','{new_request_id}',p.plan_version,
  p.schema_version,p.plan_json || jsonb_build_object(
    'runId','{new_run_id}','requestId','{new_request_id}','planId','{new_plan_id}'
  ),null,null,p.quality_gate,
  p.model_provider,p.model_name,p.prompt_version
from public.research_plan_versions p where p.research_run_id='{source_run_id}'
order by p.plan_version desc limit 1;

insert into public.research_assessments (
  id,user_id,research_run_id,plan_id,plan_version,schema_version,overall_status,
  assessment_json,model_provider,model_name,prompt_version,input_tokens,
  output_tokens,latency_ms,cost_usd
)
select '{new_assessment_id}',a.user_id,'{new_run_id}','{new_plan_id}',a.plan_version,
  a.schema_version,a.overall_status,a.assessment_json || jsonb_build_object(
    'runId','{new_run_id}','planId','{new_plan_id}','assessmentId','{new_assessment_id}'
  ),a.model_provider,a.model_name,a.prompt_version,a.input_tokens,a.output_tokens,
  a.latency_ms,a.cost_usd
from public.research_assessments a where a.research_run_id='{source_run_id}'
order by a.created_at desc limit 1;

insert into public.research_run_evidence (
  user_id,research_run_id,evidence_id,asset_id,revision_id,retrieval_rank,
  retrieval_score,retrieval_method,query_hits
)
select user_id,'{new_run_id}',evidence_id,asset_id,revision_id,retrieval_rank,
  retrieval_score,retrieval_method,query_hits
from public.research_run_evidence where research_run_id='{source_run_id}';

insert into public.research_checkpoints (
  user_id,research_run_id,job_id,lease_generation,checkpoint_version,
  execution_phase,plan_id,assessment_id,state_json
)
select c.user_id,'{new_run_id}','{new_job_id}',1,1,'synthesizing',
  '{new_plan_id}','{new_assessment_id}',
  c.state_json || jsonb_build_object(
    'runId','{new_run_id}','checkpointVersion',1,
    'operationIds','[]'::jsonb,'assessmentIds',jsonb_build_array('{new_assessment_id}'),
    'budget',jsonb_build_object(
      'limitUsd',1.0000,'protectedFinalReportReserveUsd',{protected_usd},
      'reservedUsd',0.0000,'actualUsd',0.0000,'uncertainUsd',0.0000
    )
  )
from public.research_checkpoints c where c.research_run_id='{source_run_id}'
order by c.checkpoint_version desc limit 1;
commit;
"""
    _psql(sql)
    return new_run_id


def _coordinator(gateway: RetrievalSupabaseGateway, finalizer: DeepFinalizationEngine) -> DeepResearchCoordinator:
    return DeepResearchCoordinator(
        gateway,
        ForbiddenEarlierStage("R1"),
        planner=ForbiddenEarlierStage("R2B"),
        evaluator=ForbiddenEarlierStage("R2C"),
        controller=ForbiddenEarlierStage("R2D"),
        finalizer=finalizer,
        require_provider_success=True,
    )


def _execute(source_run_id: UUID, finalizer: DeepFinalizationEngine) -> UUID:
    gateway = RetrievalSupabaseGateway.from_environment()
    new_run_id = _seed_replay(source_run_id, finalizer.maximum_cost_usd())
    worker_id = "controlled-finalization-replay"
    job = gateway.claim(worker_id, 600, "research")
    if job is None or job.payload.input.get("researchRunId") != str(new_run_id):
        raise RuntimeError("The isolated replay job was not claimed exactly once")
    coordinator = _coordinator(gateway, finalizer)
    try:
        protected = coordinator._ensure_finalization_reserve(job, worker_id)
        if protected is None or protected.get("status") != "protected":
            raise RuntimeError("Formal finalization reserve was not protected")
        bundle = gateway.load_deep_research_state(job, worker_id)
        if bundle is None:
            raise RuntimeError("Seeded replay checkpoint is unavailable")
        request = ResearchRequestV1.model_validate(bundle["request"])
        plan = ResearchPlanV1.model_validate(bundle["plan"])
        assessment = EvidenceAssessmentV1.model_validate(bundle["assessment"])
        state = ResearchIterationStateV1.model_validate(bundle["state"])
        records = _evidence_records(list(bundle.get("evidence") or []), request)
        if request.runId != new_run_id or state.executionPhase != "synthesizing":
            raise RuntimeError("Seeded replay did not reconstruct the finalization boundary")
        if bundle.get("operations"):
            raise RuntimeError("Replay run unexpectedly contains prior paid operations")
        preflight = finalizer.preflight(
            new_run_id,
            request.originalQuestion,
            plan.model_dump(mode="json"),
            _publication_rows(records),
            assessment.model_dump(mode="json"),
        )
        statuses = coordinator._reserve_finalization(
            job, worker_id, request, preflight.slots
        )
        if set(statuses.values()) != {"reserved"}:
            raise RuntimeError("Formal finalization slots were not atomically reserved")
        print(json.dumps({
            "event": "budget_reservation_verified",
            "replayRunId": str(new_run_id),
            "slotCount": len(statuses),
            "reservedUsd": format(preflight.maximum_cost_usd, "f"),
            "protectedUsd": format(finalizer.maximum_cost_usd(), "f"),
        }, separators=(",", ":")), flush=True)
        result = coordinator.process_claimed(job, worker_id, 600)
        if result.status != "completed":
            raise RuntimeError(f"Finalization replay ended with {result.status}")
    except Exception as error:
        error_code = error.code if isinstance(error, GatewayError) else "finalization_replay_failed"
        gateway.report_failure(
            job,
            worker_id,
            error_class="permanent",
            error_code=error_code,
            error_message=str(error),
            retryable=False,
        )
        print(json.dumps({
            "event": "failed", "sourceRunId": str(source_run_id),
            "replayRunId": str(new_run_id), "errorCode": error_code,
            "diagnostic": str(error)[:500],
        }, ensure_ascii=False, separators=(",", ":")), flush=True)
        raise
    print(json.dumps({
        "event": "completed", "sourceRunId": str(source_run_id),
        "replayRunId": str(new_run_id), "jobId": job.id,
        "noteId": result.note_id,
    }, separators=(",", ":")), flush=True)
    return new_run_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run-id", type=UUID, default=SOURCE_RUN_DEFAULT)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    _load_provider_environment()
    _assert_local_policy(paid=not args.preflight_only)
    finalizer = _build_finalizer()
    snapshot = _source_snapshot(args.source_run_id)
    report = _offline_preflight(snapshot, finalizer)
    print(json.dumps({"event": "preflight", **report}, separators=(",", ":")), flush=True)
    if not args.preflight_only:
        _execute(args.source_run_id, finalizer)


if __name__ == "__main__":
    main()
