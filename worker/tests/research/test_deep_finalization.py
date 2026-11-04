from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from personal_brain.llm_client import (
    LLMUsage,
    StructuredLLMError,
    StructuredLLMResult,
)
from personal_brain.research.deep_coordinator import DeepResearchCoordinator
from personal_brain.research.deep_contracts import ResearchPlanV1
from personal_brain.research.deep_finalization import (
    DeepFinalizationEngine,
    FinalizationPayloadLimitError,
    FinalizationLimits,
    _deduplicated_evidence,
    _payload_token_upper_bound,
)
from personal_brain.research.deep_planning import (
    DeepPlanningResult,
    PlanQualityDecision,
)
from personal_brain.research.deep_llm import VerifiedPricing
from personal_brain.research.llm import OpenAICompatibleResearchModel
from personal_brain.research.orchestrator import ResearchOrchestrator
from personal_brain.research.verifier import LLMClaimJudge
from personal_brain.queue_runtime import LeaseLostError
from personal_brain.supabase_gateway import GatewayError
from worker.tests.research.test_deep_coordinator import (
    FakeGateway,
    FakeRetrieval,
    ProgressiveEvaluator,
    evidence_row,
    job,
    plan_json,
    request_json,
)


class QueueClient:
    provider = "fixture-provider"
    model = "fixture-model"
    base_url = "https://example.test/v1"
    api_key = "test-only"
    max_output_tokens = 512
    timeout_seconds = 30

    def __init__(self, outputs: list[dict]) -> None:
        self.outputs = [deepcopy(value) for value in outputs]
        self.calls = 0
        self.requests: list[dict] = []

    def generate_with_metadata(self, **kwargs) -> StructuredLLMResult:
        self.calls += 1
        self.requests.append(deepcopy(kwargs))
        return StructuredLLMResult(
            output=self.outputs.pop(0),
            provider=self.provider,
            model=self.model,
            usage=LLMUsage(100, 50, 150),
            latency_ms=12,
            request_id=f"request-{self.calls}",
            service_tier="default",
            finish_reason="stop",
        )


class StaticPlanningProvider:
    def __init__(self) -> None:
        self.calls = 0

    def max_operation_cost(self, _request, _context) -> Decimal:
        return Decimal("0.0759")

    def plan(self, request, _context, *, diagnostic_sink=None):
        del diagnostic_sink
        self.calls += 1
        return DeepPlanningResult(
            plan=ResearchPlanV1.model_validate(
                plan_json(request.model_dump(mode="json"))
            ),
            quality=PlanQualityDecision("acceptable", ()),
            prompt_version="offline-full-pipeline-v1",
            model_provider="mock-provider",
            model_name="mock-planner",
            repaired=False,
            fallback_used=False,
            usage=LLMUsage(120, 80, 200),
            actual_cost_usd=Decimal("0.0010"),
            latency_ms=10,
            request_ids=("mock-r2b-request",),
            finish_reasons=("stop",),
        )


class FinalizationGateway(FakeGateway):
    def __init__(self, request, plan, catalog) -> None:
        super().__init__(request, plan, catalog)
        self.final_artifacts: dict[str, dict] = {}
        self.publications: dict[str, dict] = {}
        self.results: list[dict] = []
        self.crash_before_commit_once = False
        self.crash_after_commit_once = False
        self.crash_after_artifact_type_once: str | None = None
        self.final_generations: dict[str, int] = {}
        self.crash_after_publication_once = False
        self.crash_before_publishing_checkpoint_once = False
        self.publication_calls = 0
        self.final_reserve_requests: list[str] = []

    def ensure_deep_research_final_reserve(
        self, _claimed, _worker, *, required_reserve_usd
    ):
        self.final_reserve_requests.append(required_reserve_usd)
        budget = {
            "status": "protected",
            "limitUsd": "1.0000",
            "protectedFinalReportReserveUsd": required_reserve_usd,
            "reservedUsd": "0",
            "actualUsd": "0",
            "uncertainUsd": "0",
        }
        self.bundle["budget"] = deepcopy(budget)
        return budget

    def reserve_deep_research_finalization(self, claimed, worker, *, slots):
        operations = []
        for slot in slots:
            operation_id = slot["operationId"]
            if (
                self.operations.get(operation_id) == "running"
                and self.final_generations.get(operation_id) != claimed.lease_generation
            ):
                self.operations[operation_id] = "outcome_unknown"
            result = self.reserve_deep_research_operation(
                claimed,
                worker,
                operation_id=slot["operationId"],
                idempotency_key=slot["idempotencyKey"],
                action_type=slot["actionType"],
                cost_category=slot["costCategory"],
                parameters_hash=slot["parametersHash"],
                estimated_cost_usd=slot["estimatedCostUsd"],
            )
            self.final_generations.setdefault(operation_id, claimed.lease_generation)
            operations.append(result | {"idempotencyKey": slot["idempotencyKey"]})
        return {"operations": operations}

    def load_deep_research_finalization(self, *_args):
        return {
            "artifacts": [deepcopy(value) for value in self.final_artifacts.values()],
            "operations": [],
        }

    def complete_deep_research_final_operation(
        self, claimed, worker, **payload
    ):
        operation_id = payload["p_operation_id"]
        if self.crash_before_commit_once:
            self.crash_before_commit_once = False
            raise LeaseLostError("simulated crash before atomic artifact settlement")
        artifact = {
            "artifactId": str(uuid4()),
            "operationId": operation_id,
            "artifactType": payload["p_artifact_type"],
            "artifactKey": payload["p_artifact_key"],
            "artifact": deepcopy(payload["p_artifact"]),
            "outputHash": payload["p_output_hash"],
            "provider": payload["p_provider"],
            "modelName": payload["p_model_name"],
            "requestId": payload["p_request_id"],
            "finishReason": payload["p_finish_reason"],
            "inputTokens": payload["p_input_tokens"],
            "outputTokens": payload["p_output_tokens"],
            "reasoningTokens": payload["p_reasoning_tokens"],
            "actualCostUsd": payload["p_actual_cost_usd"],
            "latencyMs": payload["p_latency_ms"],
        }
        self.final_artifacts[operation_id] = artifact
        self.settle_deep_research_operation(
            claimed,
            worker,
            operation_id=operation_id,
            status="completed",
            actual_cost_usd=payload["p_actual_cost_usd"],
            result_ref=payload["p_result_ref"],
            error_code=None,
        )
        if self.crash_after_commit_once or (
            self.crash_after_artifact_type_once == payload["p_artifact_type"]
        ):
            self.crash_after_commit_once = False
            self.crash_after_artifact_type_once = None
            raise LeaseLostError("simulated lost response after atomic artifact settlement")
        return {"operationId": operation_id, "status": "completed"}

    def publish_research_draft(self, _job, _worker, **payload):
        self.publication_calls += 1
        publication = self.publications.setdefault(
            payload["output_hash"], {"noteId": str(uuid4()), **deepcopy(payload)}
        )
        if self.crash_after_publication_once:
            self.crash_after_publication_once = False
            raise LeaseLostError("simulated lost publication response")
        return {"noteId": publication["noteId"]}

    publish_deep_research_draft = publish_research_draft

    def commit_deep_research_checkpoint(self, claimed, worker, **kwargs):
        if (
            self.crash_before_publishing_checkpoint_once
            and kwargs["state"]["executionPhase"] == "publishing"
        ):
            self.crash_before_publishing_checkpoint_once = False
            raise LeaseLostError("simulated lost publishing checkpoint response")
        return super().commit_deep_research_checkpoint(claimed, worker, **kwargs)

    def record_deep_research_result(self, _job, _worker, **payload):
        self.results.append(deepcopy(payload))
        return {"resultStatus": payload["result_status"]}


def draft(evidence_id: str, quote: str) -> dict:
    return {
        "schemaVersion": "atomic-research-v1",
        "title": "Verified comparison",
        "summary": "A source-grounded comparison.",
        "findings": [{
            "findingId": "finding-1",
            "topic": "Comparison",
            "claims": [{
                "claimId": "claim-1",
                "statement": quote,
                "claimType": "fact",
                "citations": [{
                    "evidenceId": evidence_id,
                    "quote": quote,
                    "relationType": "supports",
                }],
            }],
        }],
    }


def two_claim_draft(first_id: str, first_quote: str, second_id: str, second_quote: str) -> dict:
    value = draft(first_id, first_quote)
    value["findings"][0]["claims"].append({
        "claimId": "claim-2",
        "statement": second_quote,
        "claimType": "fact",
        "citations": [{
            "evidenceId": second_id,
            "quote": second_quote,
            "relationType": "supports",
        }],
    })
    return value


def finalizer(draft_output: dict) -> tuple[DeepFinalizationEngine, QueueClient, QueueClient]:
    generator_client = QueueClient([draft_output])
    verifier_client = QueueClient([{
        "claimId": "claim-1",
        "verdict": "supported",
        "explanation": "The cited source directly supports the claim.",
    }])
    generator = OpenAICompatibleResearchModel(generator_client)
    engine = DeepFinalizationEngine(
        ResearchOrchestrator(generator, LLMClaimJudge(verifier_client)),
        VerifiedPricing(Decimal("2"), Decimal("10")),
        FinalizationLimits(
            max_claims=2,
            synthesis_input_tokens=10000,
            synthesis_output_tokens=512,
            verification_input_tokens=4000,
            verification_output_tokens=256,
            repair_input_tokens=12000,
            repair_output_tokens=512,
            reverification_input_tokens=4000,
            reverification_output_tokens=256,
        ),
    )
    return engine, generator_client, verifier_client


def test_preflight_reserves_worst_case_initial_repair_and_reverification() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    engine, _, _ = finalizer(draft(str(uuid4()), "source statement"))

    preflight = engine.preflight(
        run_id, request["originalQuestion"], plan_json(request), []
    )

    assert [slot.cost_category for slot in preflight.slots] == [
        "r2a", "r2e", "r2e", "r2f", "r2f", "r2f",
    ]
    assert preflight.maximum_cost_usd == sum(
        (slot.estimated_cost_usd for slot in preflight.slots), Decimal("0")
    )
    assert preflight.maximum_cost_usd > Decimal("0")


def test_payload_byte_count_is_a_conservative_token_upper_bound() -> None:
    payload = {"evidence": "證據" * 1000}
    serialized_bytes = len(
        __import__("json").dumps(
            {"system": "", "input": payload},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )

    assert _payload_token_upper_bound(payload) == serialized_bytes
    assert serialized_bytes > len(payload["evidence"])


def test_r2a_preflight_reports_safe_observed_bound_and_limit() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    engine, _, _ = finalizer(draft(str(uuid4()), "source statement"))
    engine.limits = replace(engine.limits, synthesis_input_tokens=256)

    with pytest.raises(FinalizationPayloadLimitError) as caught:
        engine.preflight(
            run_id,
            request["originalQuestion"],
            plan_json(request),
            [{
                "id": str(uuid4()),
                "origin": "internal",
                "assetId": str(uuid4()),
                "revisionId": str(uuid4()),
                "excerpt": "bounded evidence",
                "locator": {"kind": "pdf_page", "page_number": 1},
            }],
        )

    assert caught.value.stage == "R2A synthesis"
    assert caught.value.observed_upper_bound > 256
    assert caught.value.configured_limit == 256
    assert "bounded evidence" not in str(caught.value)


def test_deep_synthesis_compacts_execution_metadata_and_deduplicates_evidence() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    evidence_id = str(uuid4())
    row = {
        "id": evidence_id,
        "origin": "internal",
        "assetId": str(uuid4()),
        "revisionId": str(uuid4()),
        "excerpt": "source statement",
        "locator": {"kind": "pdf_page", "page_number": 3},
    }
    engine, generator_client, _ = finalizer(
        draft(evidence_id, "source statement")
    )

    engine.synthesis_call(
        request["originalQuestion"],
        plan_json(request),
        [row, deepcopy(row)],
    )

    sent = generator_client.requests[0]["user_input"]
    assert len(sent["evidence"]) == 1
    assert sent["evidence"][0]["locator"]["page_number"] == 3
    assert "retrievalProposals" not in sent["plan"]
    assert "requestId" not in sent["plan"]
    assert sent["plan"]["evidenceRequirements"]

    conflicting = deepcopy(row)
    conflicting["excerpt"] = "different content"
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        _deduplicated_evidence([row, conflicting])


def test_deep_synthesis_communicates_reserved_total_claim_limit() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    engine, generator_client, _ = finalizer(
        draft(str(uuid4()), "source statement")
    )

    engine.synthesis_call(
        request["originalQuestion"], plan_json(request), []
    )

    sent = generator_client.requests[0]
    assert sent["user_input"]["executionConstraints"] == {
        "maxTotalClaims": engine.limits.max_claims
    }
    assert (
        f"no more than {engine.limits.max_claims} total claims"
        in sent["system_prompt"]
    )


def test_legacy_synthesis_payload_is_unchanged_without_deep_limit() -> None:
    request = request_json(uuid4(), uuid4())
    client = QueueClient([draft(str(uuid4()), "source statement")])

    OpenAICompatibleResearchModel(client).synthesize_with_metadata(
        request["originalQuestion"], plan_json(request), []
    )

    sent = client.requests[0]
    assert "executionConstraints" not in sent["user_input"]
    assert "hard execution limit" not in sent["system_prompt"]


@pytest.mark.parametrize(
    ("claim_count", "expected"),
    [(1, "0.3066"), (3, "0.3694"), (6, "0.4636")],
)
def test_default_finalization_worst_case_by_claim_count(
    claim_count: int, expected: str
) -> None:
    engine = DeepFinalizationEngine(
        ResearchOrchestrator(
            OpenAICompatibleResearchModel(QueueClient([])),
            LLMClaimJudge(QueueClient([])),
        ),
        VerifiedPricing(Decimal("2"), Decimal("10")),
        FinalizationLimits(6, 48000, 2560, 4000, 768, 64000, 2560, 4000, 768),
    )

    assert engine.maximum_cost_usd(claim_count) == Decimal(expected)


def test_offline_full_pipeline_from_r2b_through_idempotent_publication() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    gateway = FinalizationGateway(
        request, None, {first["id"]: first, second["id"]: second}
    )
    planner = StaticPlanningProvider()
    engine, generator_client, verifier_client = finalizer(
        draft(first["id"], first["excerpt"])
    )

    result = DeepResearchCoordinator(
        gateway,
        FakeRetrieval({"first record": [first], "second record": [second]}),
        planner=planner,
        evaluator=ProgressiveEvaluator(),
        finalizer=engine,
    ).process_claimed(job(run_id, user_id), "worker-full-e2e", 120)

    assert result.status == "completed"
    assert planner.calls == 1
    assert generator_client.calls == 1
    assert verifier_client.calls == 1
    assert len(gateway.publications) == 1
    assert gateway.final_reserve_requests == [
        format(engine.maximum_cost_usd(), "f")
    ]
    assert gateway.checkpoints[0]["budget"][
        "protectedFinalReportReserveUsd"
    ] == format(engine.maximum_cost_usd(), "f")
    assert {item["cost_category"] for item in gateway.reservations} >= {
        "r2b", "r2c", "r2a", "r2e", "r2f"
    }
    assert gateway.checkpoints[-1]["executionPhase"] == "publishing"


def test_r2a_payload_limit_keeps_specific_safe_job_diagnostic() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    gateway = FinalizationGateway(
        request, None, {first["id"]: first, second["id"]: second}
    )
    planner = StaticPlanningProvider()
    engine, generator_client, _ = finalizer(
        draft(first["id"], first["excerpt"])
    )
    engine.limits = replace(engine.limits, synthesis_input_tokens=256)

    with pytest.raises(GatewayError) as caught:
        DeepResearchCoordinator(
            gateway,
            FakeRetrieval({"first record": [first], "second record": [second]}),
            planner=planner,
            evaluator=ProgressiveEvaluator(),
            finalizer=engine,
        ).process_claimed(job(run_id, user_id), "worker-payload-limit", 120)

    assert caught.value.code == "deep_finalization_payload_limit"
    assert "R2A synthesis payload token upper bound" in str(caught.value)
    assert "first source" not in str(caught.value)
    assert generator_client.calls == 0


def test_infeasible_full_envelope_blocks_before_r2b_provider_boundary() -> None:
    class InfeasibleGateway(FinalizationGateway):
        def ensure_deep_research_final_reserve(self, *_args, **_kwargs):
            return {"status": "budget_exhausted"}

    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    planner = StaticPlanningProvider()
    engine, generator_client, verifier_client = finalizer(
        draft(str(uuid4()), "source statement")
    )

    with pytest.raises(GatewayError) as caught:
        DeepResearchCoordinator(
            InfeasibleGateway(request, None, {}),
            FakeRetrieval({}),
            planner=planner,
            evaluator=ProgressiveEvaluator(),
            finalizer=engine,
        ).process_claimed(job(run_id, user_id), "worker-budget-block", 120)

    assert caught.value.code == "deep_finalization_budget_infeasible"
    assert planner.calls == 0
    assert generator_client.calls == 0
    assert verifier_client.calls == 0


def test_full_pipeline_crash_resume_does_not_repeat_any_paid_provider_call() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    gateway = FinalizationGateway(
        request, None, {first["id"]: first, second["id"]: second}
    )
    gateway.crash_after_commit_once = True
    planner = StaticPlanningProvider()
    engine, generator_client, verifier_client = finalizer(
        draft(first["id"], first["excerpt"])
    )
    coordinator = DeepResearchCoordinator(
        gateway,
        FakeRetrieval({"first record": [first], "second record": [second]}),
        planner=planner,
        evaluator=ProgressiveEvaluator(),
        finalizer=engine,
    )
    claimed = job(run_id, user_id)

    first_result = coordinator.process_claimed(claimed, "worker-crash", 120)
    second_result = coordinator.process_claimed(
        replace(claimed, lease_generation=8), "worker-resume", 120
    )

    assert first_result.status == "lease_lost"
    assert second_result.status == "completed"
    assert planner.calls == 1
    assert generator_client.calls == 1
    assert verifier_client.calls == 1
    assert len(gateway.publications) == 1


def test_deep_finalization_reserves_every_paid_boundary_and_publishes_once() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    gateway = FinalizationGateway(
        request,
        plan_json(request),
        {first["id"]: first, second["id"]: second},
    )
    retrieval = FakeRetrieval({"first record": [first], "second record": [second]})
    engine, generator_client, verifier_client = finalizer(
        draft(first["id"], first["excerpt"])
    )

    result = DeepResearchCoordinator(
        gateway,
        retrieval,
        evaluator=ProgressiveEvaluator(),
        finalizer=engine,
    ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert result.status == "completed"
    assert generator_client.calls == 1
    assert verifier_client.calls == 1
    assert len(gateway.publications) == 1
    final_categories = [
        item["cost_category"] for item in gateway.reservations
        if item["cost_category"] in {"r2a", "r2e", "r2f"}
    ]
    assert final_categories == ["r2a", "r2e", "r2e", "r2f", "r2f", "r2f"]
    assert [row["executionPhase"] for row in gateway.checkpoints[-3:]] == [
        "synthesizing", "verifying", "publishing",
    ]
    completed = [
        row for row in gateway.settlements
        if row["status"] == "completed"
        and row["operation_id"] in gateway.final_artifacts
    ]
    cancelled = [
        row for row in gateway.settlements if row["status"] == "cancelled"
    ]
    assert len(completed) == 2
    assert len(cancelled) == 4
    assert {item["artifactType"] for item in gateway.final_artifacts.values()} == {
        "draft", "verification",
    }


def test_crash_after_atomic_settlement_reuses_draft_without_second_paid_call() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    gateway = FinalizationGateway(
        request, plan_json(request), {first["id"]: first, second["id"]: second}
    )
    gateway.crash_after_commit_once = True
    engine, generator_client, verifier_client = finalizer(
        draft(first["id"], first["excerpt"])
    )
    coordinator = DeepResearchCoordinator(
        gateway,
        FakeRetrieval({"first record": [first], "second record": [second]}),
        evaluator=ProgressiveEvaluator(),
        finalizer=engine,
    )
    claimed = job(run_id, user_id)

    first_result = coordinator.process_claimed(claimed, "worker-1", 120)
    second_result = coordinator.process_claimed(
        replace(claimed, lease_generation=8), "worker-2", 120
    )

    assert first_result.status == "lease_lost"
    assert second_result.status == "completed"
    assert generator_client.calls == 1
    assert verifier_client.calls == 1
    assert len(gateway.publications) == 1


def test_lease_takeover_marks_inflight_call_unknown_and_never_replays_provider() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    gateway = FinalizationGateway(
        request, plan_json(request), {first["id"]: first, second["id"]: second}
    )
    gateway.crash_before_commit_once = True
    engine, generator_client, verifier_client = finalizer(
        draft(first["id"], first["excerpt"])
    )
    coordinator = DeepResearchCoordinator(
        gateway,
        FakeRetrieval({"first record": [first], "second record": [second]}),
        evaluator=ProgressiveEvaluator(),
        finalizer=engine,
    )
    claimed = job(run_id, user_id)

    first_result = coordinator.process_claimed(claimed, "worker-1", 120)
    with pytest.raises(GatewayError) as caught:
        coordinator.process_claimed(
            replace(claimed, lease_generation=8), "worker-2", 120
        )

    assert first_result.status == "lease_lost"
    assert caught.value.code == "deep_finalization_reconciliation_required"
    assert generator_client.calls == 1
    assert verifier_client.calls == 0
    assert "outcome_unknown" in gateway.operations.values()


def test_repair_and_reverification_use_separately_reserved_r2f_operations() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    initial = two_claim_draft(
        first["id"], first["excerpt"], second["id"], second["excerpt"]
    )
    repaired = draft(first["id"], first["excerpt"])
    generator_client = QueueClient([initial, repaired])
    verifier_client = QueueClient([
        {"claimId": "claim-1", "verdict": "supported", "explanation": "supported"},
        {"claimId": "claim-2", "verdict": "insufficient_evidence", "explanation": "insufficient"},
        {"claimId": "claim-1", "verdict": "supported", "explanation": "supported again"},
    ])
    engine = DeepFinalizationEngine(
        ResearchOrchestrator(
            OpenAICompatibleResearchModel(generator_client),
            LLMClaimJudge(verifier_client),
        ),
        VerifiedPricing(Decimal("2"), Decimal("10")),
        FinalizationLimits(2, 10000, 512, 4000, 256, 12000, 512, 4000, 256),
    )
    gateway = FinalizationGateway(
        request, plan_json(request), {first["id"]: first, second["id"]: second}
    )

    result = DeepResearchCoordinator(
        gateway,
        FakeRetrieval({"first record": [first], "second record": [second]}),
        evaluator=ProgressiveEvaluator(),
        finalizer=engine,
    ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert result.status == "completed"
    assert generator_client.calls == 2
    assert verifier_client.calls == 3
    phases = [row["executionPhase"] for row in gateway.checkpoints]
    assert "repairing" in phases
    assert phases[-2:] == ["verifying", "publishing"]
    assert {item["artifactType"] for item in gateway.final_artifacts.values()} == {
        "draft", "verification", "repaired_draft", "reverification",
    }


def test_finalization_budget_exhaustion_blocks_before_every_provider_call() -> None:
    class ExhaustedGateway(FinalizationGateway):
        def reserve_deep_research_finalization(self, *_args, **_kwargs):
            raise GatewayError(
                "deep_finalization_budget_exhausted",
                "final reserve cannot cover worst-case execution",
                retryable=False,
            )

    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    gateway = ExhaustedGateway(
        request, plan_json(request), {first["id"]: first, second["id"]: second}
    )
    engine, generator_client, verifier_client = finalizer(
        draft(first["id"], first["excerpt"])
    )

    with pytest.raises(GatewayError) as caught:
        DeepResearchCoordinator(
            gateway,
            FakeRetrieval({"first record": [first], "second record": [second]}),
            evaluator=ProgressiveEvaluator(),
            finalizer=engine,
        ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert caught.value.code == "deep_finalization_budget_exhausted"
    assert generator_client.calls == 0
    assert verifier_client.calls == 0
    assert not gateway.final_artifacts


def test_post_provider_contract_rejection_preserves_usage_and_does_not_persist_artifact() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    invalid = draft(first["id"], first["excerpt"])
    for index in (2, 3):
        invalid["findings"][0]["claims"].append({
            "claimId": f"claim-{index}",
            "statement": first["excerpt"],
            "claimType": "fact",
            "citations": [{
                "evidenceId": first["id"],
                "quote": first["excerpt"],
                "relationType": "supports",
            }],
        })
    gateway = FinalizationGateway(
        request, plan_json(request), {first["id"]: first, second["id"]: second}
    )
    engine, generator_client, verifier_client = finalizer(invalid)

    with pytest.raises(GatewayError) as caught:
        DeepResearchCoordinator(
            gateway,
            FakeRetrieval({"first record": [first], "second record": [second]}),
            evaluator=ProgressiveEvaluator(),
            finalizer=engine,
        ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert caught.value.code == "deep_finalization_contract_invalid"
    failed = [row for row in gateway.settlements if row["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["error_code"] == "final_contract_invalid"
    assert failed[0]["actual_cost_usd"] == "0.0007"
    assert failed[0]["result_ref"] is not None
    assert generator_client.calls == 1
    assert verifier_client.calls == 0
    assert not gateway.final_artifacts


def test_unknown_provider_outcome_keeps_accounting_metadata_and_is_never_retried() -> None:
    class UnknownOutcomeClient(QueueClient):
        def __init__(self) -> None:
            super().__init__([])

        def generate_with_metadata(self, **_kwargs) -> StructuredLLMResult:
            self.calls += 1
            raise StructuredLLMError(
                "simulated timeout after provider acceptance",
                error_kind="timeout",
                outcome_unknown=True,
                usage=LLMUsage(100, 50, 150),
                latency_ms=30_000,
                request_id="request-unknown",
            )

    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    gateway = FinalizationGateway(
        request, plan_json(request), {first["id"]: first, second["id"]: second}
    )
    generator_client = UnknownOutcomeClient()
    verifier_client = QueueClient([])
    engine = DeepFinalizationEngine(
        ResearchOrchestrator(
            OpenAICompatibleResearchModel(generator_client),
            LLMClaimJudge(verifier_client),
        ),
        VerifiedPricing(Decimal("2"), Decimal("10")),
        FinalizationLimits(2, 10000, 512, 4000, 256, 12000, 512, 4000, 256),
    )
    coordinator = DeepResearchCoordinator(
        gateway,
        FakeRetrieval({"first record": [first], "second record": [second]}),
        evaluator=ProgressiveEvaluator(),
        finalizer=engine,
    )
    claimed = job(run_id, user_id)

    with pytest.raises(GatewayError) as first_error:
        coordinator.process_claimed(claimed, "worker-1", 120)
    with pytest.raises(GatewayError) as replay_error:
        coordinator.process_claimed(
            replace(claimed, lease_generation=8), "worker-2", 120
        )

    assert first_error.value.code == "deep_finalization_provider_outcome_unknown"
    assert replay_error.value.code == "deep_finalization_reconciliation_required"
    unknown = [
        row for row in gateway.settlements if row["status"] == "outcome_unknown"
    ]
    assert len(unknown) == 1
    assert unknown[0]["actual_cost_usd"] == "0.0007"
    assert "request-unknown" in unknown[0]["result_ref"]
    assert generator_client.calls == 1
    assert not gateway.final_artifacts


def test_lost_publication_response_replays_idempotently_without_paid_calls() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    gateway = FinalizationGateway(
        request, plan_json(request), {first["id"]: first, second["id"]: second}
    )
    gateway.crash_after_publication_once = True
    engine, generator_client, verifier_client = finalizer(
        draft(first["id"], first["excerpt"])
    )
    coordinator = DeepResearchCoordinator(
        gateway,
        FakeRetrieval({"first record": [first], "second record": [second]}),
        evaluator=ProgressiveEvaluator(),
        finalizer=engine,
    )
    claimed = job(run_id, user_id)

    first_result = coordinator.process_claimed(claimed, "worker-1", 120)
    second_result = coordinator.process_claimed(
        replace(claimed, lease_generation=8), "worker-2", 120
    )

    assert first_result.status == "lease_lost"
    assert second_result.status == "completed"
    assert generator_client.calls == 1
    assert verifier_client.calls == 1
    assert gateway.publication_calls == 2
    assert len(gateway.publications) == 1


def test_crash_after_unused_slot_release_before_checkpoint_resumes_safely() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    gateway = FinalizationGateway(
        request, plan_json(request), {first["id"]: first, second["id"]: second}
    )
    gateway.crash_before_publishing_checkpoint_once = True
    engine, generator_client, verifier_client = finalizer(
        draft(first["id"], first["excerpt"])
    )
    coordinator = DeepResearchCoordinator(
        gateway,
        FakeRetrieval({"first record": [first], "second record": [second]}),
        evaluator=ProgressiveEvaluator(),
        finalizer=engine,
    )
    claimed = job(run_id, user_id)

    first_result = coordinator.process_claimed(claimed, "worker-1", 120)
    second_result = coordinator.process_claimed(
        replace(claimed, lease_generation=8), "worker-2", 120
    )

    assert first_result.status == "lease_lost"
    assert second_result.status == "completed"
    assert generator_client.calls == 1
    assert verifier_client.calls == 1
    assert len(gateway.publications) == 1


@pytest.mark.parametrize(
    ("artifact_type", "repair_path", "expected_generator_calls", "expected_verifier_calls"),
    [
        ("draft", False, 1, 1),
        ("verification", False, 1, 1),
        ("repaired_draft", True, 2, 3),
        ("reverification", True, 2, 3),
    ],
)
def test_each_paid_boundary_reuses_atomic_result_after_lost_response(
    artifact_type: str,
    repair_path: bool,
    expected_generator_calls: int,
    expected_verifier_calls: int,
) -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    gateway = FinalizationGateway(
        request, plan_json(request), {first["id"]: first, second["id"]: second}
    )
    gateway.crash_after_artifact_type_once = artifact_type

    if repair_path:
        initial = two_claim_draft(
            first["id"], first["excerpt"], second["id"], second["excerpt"]
        )
        generator_client = QueueClient([
            initial,
            draft(first["id"], first["excerpt"]),
        ])
        verifier_client = QueueClient([
            {"claimId": "claim-1", "verdict": "supported", "explanation": "supported"},
            {
                "claimId": "claim-2",
                "verdict": "insufficient_evidence",
                "explanation": "insufficient",
            },
            {
                "claimId": "claim-1",
                "verdict": "supported",
                "explanation": "supported after repair",
            },
        ])
        engine = DeepFinalizationEngine(
            ResearchOrchestrator(
                OpenAICompatibleResearchModel(generator_client),
                LLMClaimJudge(verifier_client),
            ),
            VerifiedPricing(Decimal("2"), Decimal("10")),
            FinalizationLimits(2, 10000, 512, 4000, 256, 12000, 512, 4000, 256),
        )
    else:
        engine, generator_client, verifier_client = finalizer(
            draft(first["id"], first["excerpt"])
        )

    coordinator = DeepResearchCoordinator(
        gateway,
        FakeRetrieval({"first record": [first], "second record": [second]}),
        evaluator=ProgressiveEvaluator(),
        finalizer=engine,
    )
    claimed = job(run_id, user_id)

    first_result = coordinator.process_claimed(claimed, "worker-1", 120)
    second_result = coordinator.process_claimed(
        replace(claimed, lease_generation=8), "worker-2", 120
    )

    assert first_result.status == "lease_lost"
    assert second_result.status == "completed"
    assert generator_client.calls == expected_generator_calls
    assert verifier_client.calls == expected_verifier_calls
    assert len(gateway.publications) == 1
