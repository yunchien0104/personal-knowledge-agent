"""Offline Local-Supabase finalization replay using the retained PDF checkpoint.

This harness never constructs a network Provider client.  It reuses the saved
ResearchPlan, EvidenceAssessment, R2D checkpoint, and all 13 immutable Evidence
rows, then exercises R2A -> R2E -> R2F -> publication with deterministic mock
Structured Outputs through the production coordinator, ledger, checkpoint, and
publication RPCs.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from decimal import Decimal
import json
import os
from typing import Any
from uuid import UUID

from personal_brain.llm_client import LLMUsage, StructuredLLMResult
from personal_brain.research.deep_contracts import (
    EvidenceAssessmentV1,
    ResearchIterationStateV1,
    ResearchPlanV1,
    ResearchRequestV1,
)
from personal_brain.research.deep_finalization import (
    DeepFinalizationEngine,
    FinalizationLimits,
)
from personal_brain.research.deep_llm import VerifiedPricing
from personal_brain.research.llm import OpenAICompatibleResearchModel
from personal_brain.research.orchestrator import ResearchOrchestrator
from personal_brain.research.verifier import LLMClaimJudge
from personal_brain.retrieval_gateway import RetrievalSupabaseGateway

from worker.tests.research.run_deep_finalization_real_replay import (
    SOURCE_RUN_DEFAULT,
    _coordinator,
    _evidence_records,
    _offline_preflight,
    _psql,
    _publication_rows,
    _seed_replay,
    _source_snapshot,
)


class MockStructuredClient:
    provider = "offline-mock"
    model = "offline-finalization-v1"
    base_url = "https://offline.invalid/v1"
    api_key = "not-a-provider-key"
    max_output_tokens = 16_384
    timeout_seconds = 1

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self.outputs = outputs.copy()
        self.calls = 0

    def generate_with_metadata(self, **_kwargs: Any) -> StructuredLLMResult:
        if not self.outputs:
            raise AssertionError("Unexpected offline mock Provider call")
        self.calls += 1
        return StructuredLLMResult(
            output=self.outputs.pop(0),
            provider=self.provider,
            model=self.model,
            usage=LLMUsage(100, 50, 150),
            latency_ms=1,
            request_id=f"offline-request-{self.calls}",
            service_tier="default",
            finish_reason="stop",
        )


def _quote(excerpt: str) -> str:
    if not excerpt:
        raise RuntimeError("Retained Evidence excerpt is empty")
    # Shaping may join non-contiguous exact source segments with a presentation
    # newline.  Never create a mock quote across that join: select one visible
    # line so the same bytes remain a continuous substring of the original.
    for line in excerpt.splitlines():
        if line.strip():
            return line[:240]
    raise RuntimeError("Retained Evidence excerpt has no citable text")


def _draft(rows: list[dict[str, Any]], *, retained: tuple[int, ...]) -> dict[str, Any]:
    claims = []
    for index in retained:
        row = rows[index]
        quote = _quote(str(row["excerpt"]))
        claims.append({
            "claimId": f"claim-{index + 1}",
            "statement": quote,
            "claimType": "fact",
            "citations": [{
                "evidenceId": str(row["id"]),
                "quote": quote,
                "relationType": "supports",
            }],
        })
    return {
        "schemaVersion": "atomic-research-v1",
        "title": "Offline verification of retained PDF evidence",
        "summary": "Deterministic mock output for finalization safety verification.",
        "findings": [{
            "findingId": "finding-1",
            "topic": "Retained evidence publication path",
            "claims": claims,
        }],
    }


def _engine() -> tuple[
    DeepFinalizationEngine, MockStructuredClient, MockStructuredClient
]:
    generator_client = MockStructuredClient([])
    verifier_client = MockStructuredClient([])
    limits = replace(FinalizationLimits.from_environment(), max_claims=3)
    engine = DeepFinalizationEngine(
        ResearchOrchestrator(
            OpenAICompatibleResearchModel(
                generator_client, repair_client=generator_client
            ),
            LLMClaimJudge(verifier_client),
        ),
        VerifiedPricing(Decimal("2"), Decimal("10")),
        limits,
    )
    return engine, generator_client, verifier_client


def _set_target_outcome(run_id: UUID, target: str) -> None:
    _psql(f"""
update public.research_checkpoints
set state_json = jsonb_set(state_json, '{{targetOutcome}}', to_jsonb('{target}'::text), true)
where research_run_id='{run_id}';
update public.research_runs
set progress_json = progress_json || jsonb_build_object(
  'offlineMockPublicationTarget','{target}'
)
where id='{run_id}';
""")


def _rls_readback(run_id: UUID, note_id: str) -> dict[str, Any]:
    value = _psql(f"""
with owner as (select user_id from public.research_runs where id='{run_id}')
select jsonb_build_object(
  'ownerUserId',(select user_id from owner),
  'noteId','{note_id}',
  'claimCount',(select count(*) from public.claims where note_id='{note_id}'),
  'claimEvidenceCount',(select count(*) from public.claim_evidence ce
    join public.claims c on c.id=ce.claim_id where c.note_id='{note_id}'),
  'citationCount',(select count(*) from public.research_citations where research_run_id='{run_id}')
)::text;
""")
    result = json.loads(value)
    owner = result["ownerUserId"]
    visible = _psql(f"""
begin;
set local role authenticated;
select set_config('request.jwt.claim.sub','{owner}',true);
select jsonb_build_object(
  'notes',(select count(*) from public.notes where id='{note_id}'),
  'claims',(select count(*) from public.claims where note_id='{note_id}'),
  'claimEvidence',(select count(*) from public.claim_evidence ce
    join public.claims c on c.id=ce.claim_id where c.note_id='{note_id}'),
  'citations',(select count(*) from public.research_citations where research_run_id='{run_id}')
)::text;
rollback;
""").splitlines()[-1]
    hidden = _psql(f"""
begin;
set local role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-4000-8000-000000000099',true);
select jsonb_build_object(
  'notes',(select count(*) from public.notes where id='{note_id}'),
  'claims',(select count(*) from public.claims where note_id='{note_id}'),
  'claimEvidence',(select count(*) from public.claim_evidence ce
    join public.claims c on c.id=ce.claim_id where c.note_id='{note_id}'),
  'citations',(select count(*) from public.research_citations where research_run_id='{run_id}')
)::text;
rollback;
""").splitlines()[-1]
    result["ownerVisible"] = json.loads(visible)
    result["otherUserVisible"] = json.loads(hidden)
    return result


def run(source_run_id: UUID, *, target_outcome: str) -> dict[str, Any]:
    snapshot = _source_snapshot(source_run_id)
    if len(snapshot["evidence"]) != 13:
        raise RuntimeError("Expected exactly 13 retained PDF Evidence rows")
    original_target = str(snapshot["state"].get("targetOutcome"))
    if original_target != "qualified_partial":
        raise RuntimeError("Saved R2D target outcome changed unexpectedly")
    engine, generator_client, verifier_client = _engine()
    preflight = _offline_preflight(snapshot, engine)
    source_request = ResearchRequestV1.model_validate(snapshot["request"])
    source_records = _evidence_records(snapshot["evidence"], source_request)
    visible_rows = list(engine.preflight(
        source_run_id,
        snapshot["request"]["originalQuestion"],
        snapshot["plan"],
        _publication_rows(source_records),
        snapshot["assessment"],
    ).synthesis_evidence)
    initial = _draft(visible_rows, retained=(0, 1, 2))
    repaired = _draft(visible_rows, retained=(0, 2))
    generator_client.outputs.extend([initial, repaired])
    verifier_client.outputs.extend([
        {"claimId": "claim-1", "verdict": "supported", "explanation": "Exact cited evidence supports the claim."},
        {"claimId": "claim-2", "verdict": "insufficient_evidence", "explanation": "The mock gate requires bounded repair."},
        {"claimId": "claim-3", "verdict": "supported", "explanation": "Exact cited evidence supports the claim."},
        {"claimId": "claim-1", "verdict": "supported", "explanation": "Reverification passed."},
        {"claimId": "claim-3", "verdict": "supported", "explanation": "Reverification passed."},
    ])
    run_id = _seed_replay(source_run_id, engine.maximum_cost_usd())
    _set_target_outcome(run_id, target_outcome)

    gateway = RetrievalSupabaseGateway.from_environment()
    worker_id = f"offline-finalization-{target_outcome}"
    job = gateway.claim(worker_id, 600, "research")
    if job is None or job.payload.input.get("researchRunId") != str(run_id):
        raise RuntimeError("Offline replay job was not claimed exactly once")
    bundle = gateway.load_deep_research_state(job, worker_id)
    if bundle is None:
        raise RuntimeError("Offline replay checkpoint is unavailable")
    request = ResearchRequestV1.model_validate(bundle["request"])
    plan = ResearchPlanV1.model_validate(bundle["plan"])
    EvidenceAssessmentV1.model_validate(bundle["assessment"])
    state = ResearchIterationStateV1.model_validate(bundle["state"])
    if state.targetOutcome != target_outcome:
        raise RuntimeError("Offline replay target outcome was not isolated")
    records = _evidence_records(list(bundle["evidence"]), request)
    if len(records) != 13:
        raise RuntimeError("Offline replay lost retained Evidence")
    result = _coordinator(gateway, engine).process_claimed(job, worker_id, 600)
    if result.status != "completed":
        raise RuntimeError(f"Offline replay ended with {result.status}")

    counts = json.loads(_psql(f"""
select jsonb_build_object(
  'runStatus',(select status from public.research_runs where id='{run_id}'),
  'artifactCount',(select count(*) from public.research_finalization_artifacts where research_run_id='{run_id}'),
  'operationCount',(select count(*) from public.research_operations where research_run_id='{run_id}'),
  'manifestCount',(select count(*) from public.research_payload_manifests where research_run_id='{run_id}'),
  'resultCount',(select count(*) from public.deep_research_results where research_run_id='{run_id}'),
  'noteId',(select output_note_id from public.research_runs where id='{run_id}'),
  'actualUsd',(select actual_usd from public.research_run_budgets where research_run_id='{run_id}'),
  'reservedUsd',(select reserved_usd from public.research_run_budgets where research_run_id='{run_id}'),
  'uncertainUsd',(select uncertain_usd from public.research_run_budgets where research_run_id='{run_id}')
)::text;
"""))
    note_id = counts.get("noteId")
    rls = _rls_readback(run_id, note_id) if note_id else None
    return {
        "sourceRunId": str(source_run_id),
        "replayRunId": str(run_id),
        "savedTargetOutcome": original_target,
        "testTargetOutcome": target_outcome,
        "evidenceCount": len(records),
        "initialClaimCount": len(initial["findings"][0]["claims"]),
        "preflight": preflight,
        "database": counts,
        "rls": rls,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run-id", type=UUID, default=SOURCE_RUN_DEFAULT)
    parser.add_argument(
        "--target-outcome", choices=("qualified_partial", "complete"),
        default="qualified_partial",
    )
    args = parser.parse_args()
    db_url = os.environ.get("LOCAL_REPLAY_DB_URL", "")
    api_url = os.environ.get("SUPABASE_SERVER_URL", "")
    if "127.0.0.1:60322" not in db_url or api_url != "http://127.0.0.1:60321":
        raise RuntimeError("Offline mock replay is restricted to Local Supabase 60321/60322")
    print(json.dumps(run(args.source_run_id, target_outcome=args.target_outcome), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
