"""Explicitly authorized Full Deep Real Provider E2E entry point.

This module is intentionally not collected by pytest. It requires a freshly
seeded Local Supabase stack and delegates every cost decision to the production
payload-aware preflight plus atomic database budget RPCs.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlparse

from personal_brain.retrieval import EmbeddingSettings, HybridRetriever, RetrievalError
from personal_brain.retrieval_gateway import RetrievalSupabaseGateway
from personal_brain.research.deep_contracts import ResearchRequestV1
from personal_brain.research.deep_coordinator import DeepResearchCoordinator, _evidence_records
from personal_brain.research.deep_evaluation import EvidenceEvaluator
from personal_brain.research.deep_finalization import DeepFinalizationEngine
from personal_brain.research.deep_llm import (
    OpenAICompatibleDeepPlanningModel,
    OpenAICompatibleEvidenceEvaluationModel,
)
from personal_brain.research.deep_planning import DeepResearchPlanner, build_planning_context_pack
from personal_brain.research.llm import OpenAICompatibleResearchModel
from personal_brain.research.orchestrator import ResearchOrchestrator
from personal_brain.research.verifier import LLMClaimJudge
from personal_brain.supabase_gateway import GatewayError


class LocalLexicalProvider:
    """Use R1's production lexical fallback without creating an unledgered call."""

    settings = EmbeddingSettings(
        base_url="https://local.invalid/v1",
        api_key="local-fixture-only",
        model="controlled-local-lexical",
        version="v1",
        max_retries=0,
    )

    def embed(self, _inputs):
        raise RetrievalError(
            "controlled_lexical_only",
            "Controlled E2E uses indexed Local PostgreSQL lexical retrieval",
            retryable=False,
        )


def _assert_local_policy() -> None:
    if os.getenv("FULL_DEEP_REAL_PROVIDER_AUTHORIZED") != "true":
        raise RuntimeError("Explicit Real Provider authorization marker is missing")
    if os.getenv("FULL_DEEP_TEST_BUDGET_POLICY") != "isolated-local":
        raise RuntimeError("The isolated Local DB budget policy marker is missing")
    parsed = urlparse(os.environ.get("SUPABASE_SERVER_URL", ""))
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Full Deep E2E requires a loopback Supabase Local URL")
    if os.getenv("R2B_MODEL") != "gpt-6.1-sol" or os.getenv("R2C_MODEL") != "gpt-6.1-sol":
        raise RuntimeError("R2B and R2C must use gpt-6.1-sol")
    if os.getenv("RESEARCH_MODEL") != "gpt-6.1-sol":
        raise RuntimeError("Finalization must use gpt-6.1-sol")


def main() -> None:
    _assert_local_policy()
    worker_id = os.getenv("WORKER_ID", "controlled-full-real-e2e").strip()
    gateway = RetrievalSupabaseGateway.from_environment()
    # Validate every provider and pricing setting before claiming durable work.
    # A configuration error must not strand a job lease at the provider boundary.
    planner = DeepResearchPlanner(OpenAICompatibleDeepPlanningModel.from_environment())
    evaluator = EvidenceEvaluator(OpenAICompatibleEvidenceEvaluationModel.from_environment())
    legacy_generator = OpenAICompatibleResearchModel()
    finalizer = DeepFinalizationEngine.from_legacy_orchestrator(
        ResearchOrchestrator(legacy_generator, LLMClaimJudge(legacy_generator.client))
    )

    job = gateway.claim(worker_id, 600, "research")
    if job is None:
        raise RuntimeError("No isolated Local Deep Research job is available")

    bundle = gateway.load_deep_research_state(job, worker_id)
    if bundle is None:
        raise RuntimeError("Claimed Local Deep Research state is unavailable")
    request = ResearchRequestV1.model_validate(bundle["request"])
    records = _evidence_records(list(bundle.get("evidence") or []), request)
    planning_context = build_planning_context_pack(
        [item.model_dump(mode="json") for item in records], request
    )
    r2b_preflight = planner.max_operation_cost(request, planning_context)
    final_preflight = finalizer.maximum_cost_usd()
    print(json.dumps({
        "event": "preflight",
        "runId": str(request.runId),
        "runBudgetPolicyUsd": str(request.effectiveLimits.maxCostUsd),
        "r2bDynamicReservationUsd": str(r2b_preflight),
        "r2cReservation": "computed from the persisted plan and retrieved evidence",
        "finalizationDynamicEnvelopeUsd": str(final_preflight),
    }, separators=(",", ":")), flush=True)

    try:
        result = DeepResearchCoordinator(
            gateway,
            HybridRetriever(gateway, LocalLexicalProvider()),
            planner=planner,
            evaluator=evaluator,
            finalizer=finalizer,
            require_provider_success=True,
        ).process_claimed(job, worker_id, 600)
    except GatewayError as error:
        gateway.report_failure(
            job,
            worker_id,
            error_class="transient" if error.retryable else "permanent",
            error_code=error.code,
            error_message=str(error),
            retryable=error.retryable,
        )
        raise
    print(json.dumps({
        "event": "completed",
        "runId": str(request.runId),
        "jobId": result.job_id,
        "status": result.status,
    }, separators=(",", ":")), flush=True)
    if result.status != "completed":
        raise RuntimeError(f"Controlled Full Deep E2E ended with {result.status}")


if __name__ == "__main__":
    main()
