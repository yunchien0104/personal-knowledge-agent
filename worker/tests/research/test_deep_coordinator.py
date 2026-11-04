from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import UUID, uuid4

import pytest

from personal_brain.models import JobPayloadV1
from personal_brain.llm_client import LLMUsage
from personal_brain.research.deep_contracts import (
    EvidenceAssessmentV1,
    ResearchBudgetStateV1,
    ResearchIterationStateV1,
    ResearchPlanV1,
)
from personal_brain.research.deep_coordinator import DeepResearchCoordinator, _operation_id
from personal_brain.research.deep_diagnostics import R2CValidationDiagnostic
from personal_brain.research.deep_evaluation import EvidenceEvaluator, EvaluationResult
from personal_brain.research.deep_planning import DeepResearchPlanner, build_planning_context_pack
from personal_brain.supabase_gateway import ClaimedJob, GatewayError


def request_json(run_id: UUID, user_id: UUID) -> dict:
    return {
        "schemaVersion": "research-request-v1", "requestId": str(uuid4()),
        "runId": str(run_id), "idempotencyKey": str(uuid4()), "userId": str(user_id),
        "objective": "Compare the two internal records", "originalQuestion": "Compare the records",
        "executionMode": "deep", "internalKnowledgeScope": {"selectedAssetIds": None},
        "webPolicy": "disabled",
        "permissions": {"internalKnowledge": True, "webDiscovery": False, "webFetch": False},
        "effectiveLimits": {"maxRounds": 3, "maxQueries": 3, "maxCostUsd": "0.5", "maxDurationSeconds": 300},
        "analysisSpec": None,
    }


def plan_json(request: dict) -> dict:
    return ResearchPlanV1.model_validate({
        "schemaVersion": "research-plan-v1", "planId": str(uuid4()),
        "runId": request["runId"], "requestId": request["requestId"], "planVersion": 1,
        "normalizedResearchIntent": request["objective"], "objective": request["objective"],
        "explicitConstraints": [], "inferredAssumptions": [], "essentialClarifications": [],
        "subquestions": [{"id": "sq_01", "question": "What do the records establish?", "priority": 1,
                          "dependsOn": [], "evidenceRequirementIds": ["req_01"]}],
        "evidenceRequirements": [{"id": "req_01", "subquestionId": "sq_01",
                                  "description": "Direct evidence", "required": True,
                                  "evidenceType": "primary_source"}],
        "coverageCriteria": [{"id": "coverage_01", "description": "Direct coverage",
                              "evidenceRequirementIds": ["req_01"]}],
        "retrievalProposals": [
            {"id": "query_01", "subquestionId": "sq_01", "source": "internal",
             "query": "first record", "purpose": "Find first source"},
            {"id": "query_02", "subquestionId": "sq_01", "source": "internal",
             "query": "second record", "purpose": "Find second source"},
        ],
        "revisionParentPlanId": None, "revisionReason": None,
    }).model_dump(mode="json")


def job(run_id: UUID, user_id: UUID) -> ClaimedJob:
    job_id = uuid4()
    payload = JobPayloadV1(
        str(job_id), str(user_id), "execute_research", "deep-test",
        {"researchRunId": str(run_id), "researchMode": "deep", "question": "Compare the records"},
    )
    return ClaimedJob(str(job_id), str(user_id), None, 7, payload)


class FakeGateway:
    def __init__(self, request: dict, plan: dict, catalog: dict[str, dict]) -> None:
        self.bundle = {"request": request, "plan": plan, "state": None, "assessment": None,
                       "budget": None, "evidence": [], "operations": [],
                       "deadlineAt": "2099-01-01T00:00:00+00:00"}
        self.catalog = catalog
        self.operations: dict[str, str] = {}
        self.checkpoints: list[dict] = []
        self.appended_rounds: list[int] = []
        self.completed: dict | None = None
        self.heartbeats = 0
        self.lease_valid = True
        self.reservations: list[dict] = []
        self.settlements: list[dict] = []
        self.validation_diagnostics: list[dict] = []

    def heartbeat(self, *_args) -> bool:
        self.heartbeats += 1
        return self.lease_valid

    def load_deep_research_state(self, *_args):
        return deepcopy(self.bundle)

    def reserve_deep_research_operation(self, _job, _worker, **kwargs):
        self.reservations.append(deepcopy(kwargs))
        operation_id = kwargs["operation_id"]
        status = self.operations.get(operation_id)
        if status is None:
            status = "reserved"
            self.operations[operation_id] = status
        return {"operationId": operation_id, "status": status, "reservedCostUsd": "0"}

    def start_deep_research_operation(self, _job, _worker, operation_id):
        if self.operations.get(operation_id) != "reserved":
            return False
        self.operations[operation_id] = "running"
        return True

    def record_deep_research_validation_diagnostic(
        self, _job, _worker, *, operation_id, diagnostic
    ):
        self.validation_diagnostics.append({
            "operation_id": operation_id,
            "diagnostic": deepcopy(diagnostic),
        })
        return {"operationId": operation_id, "attemptNumber": diagnostic["attemptNumber"]}

    def record_deep_research_evaluation_diagnostic(
        self, _job, _worker, *, operation_id, diagnostic
    ):
        self.validation_diagnostics.append({
            "operation_id": operation_id,
            "diagnostic": deepcopy(diagnostic),
        })
        return {"operationId": operation_id, "attemptNumber": diagnostic["attemptNumber"]}

    def settle_deep_research_operation(self, _job, _worker, **kwargs):
        self.settlements.append(deepcopy(kwargs))
        self.operations[kwargs["operation_id"]] = kwargs["status"]
        return {"operationId": kwargs["operation_id"], "status": kwargs["status"]}

    def record_deep_research_plan(self, _job, _worker, **kwargs):
        self.bundle["plan"] = deepcopy(kwargs["plan"])
        return {"planId": kwargs["plan"]["planId"]}

    def append_deep_research_evidence(self, _job, _worker, **kwargs):
        self.appended_rounds.append(kwargs["round_number"])
        known = {row["id"] for row in self.bundle["evidence"]}
        for row in kwargs["evidence"]:
            if row["id"] not in known:
                self.bundle["evidence"].append(deepcopy(self.catalog[row["id"]]))
        return {"processedEvidence": len(kwargs["evidence"])}

    def append_deep_web_evidence(self, _job, _worker, **kwargs):
        for row in kwargs["evidence"]:
            self.bundle["evidence"].append(deepcopy(row))
        return {"processedEvidence": len(kwargs["evidence"])}

    def commit_deep_research_checkpoint(self, _job, _worker, **kwargs):
        assert kwargs["expected_checkpoint_version"] == len(self.checkpoints)
        self.checkpoints.append(deepcopy(kwargs["state"]))
        self.bundle["state"] = deepcopy(kwargs["state"])
        return {"checkpointVersion": len(self.checkpoints)}

    def record_deep_research_assessment(self, _job, _worker, assessment):
        self.bundle["assessment"] = deepcopy(assessment)
        return {"assessmentId": assessment["assessmentId"]}

    def complete_deep_research_without_publication(self, _job, _worker, **kwargs):
        self.completed = kwargs
        return {"status": "completed"}


class FakeRetrieval:
    def __init__(self, rows_by_query: dict[str, list[dict]]) -> None:
        self.rows_by_query = rows_by_query
        self.calls: list[str] = []

    def retrieve_for_job(self, _job, _worker, query, **_kwargs):
        self.calls.append(query)
        return {"semanticStatus": "ready", "results": deepcopy(self.rows_by_query[query])}


@dataclass
class ProgressiveEvaluator:
    calls: int = 0

    def assess(self, plan, records, *, user_id, diagnostic_sink=None):
        del user_id
        self.calls += 1
        satisfied = len(records) >= 2
        status = "satisfied" if satisfied else "partial"
        assessment = EvidenceAssessmentV1.model_validate({
            "schemaVersion": "evidence-assessment-v1", "assessmentId": str(uuid4()),
            "runId": str(plan.runId), "planId": str(plan.planId), "planVersion": 1,
            "evidenceQuality": [],
            "requirementCoverage": [{"requirementId": "req_01", "status": status,
                                     "sufficiency": "pass" if satisfied else "concern",
                                     "independence": "pass" if satisfied else "unknown",
                                     "supportingEvidenceIds": [str(r.evidenceId) for r in records],
                                     "contradictingEvidenceIds": [], "explanation": "bounded test"}],
            "subquestionStatus": [{"subquestionId": "sq_01", "status": status,
                                   "requirementIds": ["req_01"]}],
            "overallStatus": "sufficient" if satisfied else "partial",
            "contradiction": "pass", "independence": "pass" if satisfied else "unknown",
            "conflicts": [],
            "prioritizedGaps": [] if satisfied else [{"id": "gap_01", "requirementId": "req_01",
                "severity": "critical", "expectedInformationGain": 5, "resolvability": 5,
                "dependencyImpact": 5, "proposedAction": "search_internal", "explanation": "need another source"}],
            "explanation": "test assessment",
            "telemetry": {"modelProvider": "test", "modelName": "test", "promptVersion": "test-v1",
                          "inputTokens": 0, "outputTokens": 0, "latencyMs": 0, "costUsd": "0"},
        })
        result = EvaluationResult(assessment, repaired=False, fallback_used=False)
        if diagnostic_sink is not None:
            for diagnostic in result.diagnostics:
                diagnostic_sink(diagnostic)
        return result


@dataclass
class CountingPlanner:
    calls: int = 0

    def max_operation_cost(self, _request, _context):
        return Decimal("0.0400")

    def plan(self, _request, _context, *, diagnostic_sink=None):
        del diagnostic_sink
        self.calls += 1
        raise AssertionError("terminal operation must not re-enter R2B")


@dataclass
class CountingEvaluator(ProgressiveEvaluator):
    def max_operation_cost(self, _plan, _records, *, user_id):
        del user_id
        return Decimal("0.0500")


def assessing_state(run_id: UUID, plan: dict) -> dict:
    return ResearchIterationStateV1(
        schemaVersion="research-iteration-state-v1",
        runId=run_id,
        planVersion=plan["planVersion"],
        checkpointVersion=1,
        currentRound=1,
        completedRounds=[],
        executionPhase="assessing",
        queryFingerprints=[],
        operationIds=[],
        assessmentIds=[],
        requirementProgress={"req_01": "unknown"},
        unresolvedCriticalGapIds=[],
        proposedActions=[],
        approvedActionIds=[],
        budget=ResearchBudgetStateV1(
            limitUsd=Decimal("0.5"), protectedFinalReportReserveUsd=Decimal("0.1"),
            reservedUsd=Decimal("0"), actualUsd=Decimal("0"),
            uncertainUsd=Decimal("0"),
        ),
        consecutiveNoInformationGainRounds=0,
    ).model_dump(mode="json")


def evidence_row(evidence_id: UUID, asset_id: UUID, revision_id: UUID, text: str) -> dict:
    return {"evidenceId": str(evidence_id), "id": str(evidence_id), "assetId": str(asset_id),
            "revisionId": str(revision_id), "content": text, "excerpt": text,
            "locator": {"kind": "test", "offset": 0}, "retrievalMethod": "lexical",
            "retrievalScore": 1.0, "score": 1.0, "queryHits": 1}


def test_multi_round_accumulates_evidence_and_checkpoints() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    second = evidence_row(uuid4(), uuid4(), uuid4(), "second source")
    catalog = {first["id"]: first, second["id"]: second}
    gateway = FakeGateway(request, plan_json(request), catalog)
    retrieval = FakeRetrieval({"first record": [first], "second record": [second]})
    evaluator = ProgressiveEvaluator()

    result = DeepResearchCoordinator(
        gateway, retrieval, evaluator=evaluator
    ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert result.status == "completed"
    assert retrieval.calls == ["first record", "second record"]
    assert gateway.appended_rounds == [1, 2]
    assert {row["id"] for row in gateway.bundle["evidence"]} == set(catalog)
    assert gateway.checkpoints[-1]["executionPhase"] == "terminal"
    assert gateway.checkpoints[-1]["outcome"] == "complete"
    assert gateway.completed == {"outcome": "qualified_partial", "stop_reason": "quality_reached"}


def test_completed_operation_is_not_reexecuted_after_resume() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    catalog = {first["id"]: first}
    gateway = FakeGateway(request, plan_json(request), catalog)
    retrieval = FakeRetrieval({"first record": [first], "second record": []})
    coordinator = DeepResearchCoordinator(gateway, retrieval, evaluator=ProgressiveEvaluator())
    claimed = job(run_id, user_id)
    from personal_brain.research.deep_contracts import ResearchRequestV1
    parsed_request = ResearchRequestV1.model_validate(request)

    first_result = coordinator._retrieve(
        claimed, "worker-1", parsed_request, "first record", round_number=1
    )
    resumed_result = coordinator._retrieve(
        claimed, "worker-1", parsed_request, "first record", round_number=1
    )

    assert first_result[2] == "completed"
    assert resumed_result[2] == "completed"
    assert retrieval.calls == ["first record"]
    assert gateway.appended_rounds == [1]


def test_outcome_unknown_paid_or_external_operation_is_never_retried() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    gateway = FakeGateway(request, plan_json(request), {first["id"]: first})
    retrieval = FakeRetrieval({"first record": [first]})
    coordinator = DeepResearchCoordinator(gateway, retrieval, evaluator=ProgressiveEvaluator())
    from personal_brain.research.deep_contracts import ResearchRequestV1
    parsed_request = ResearchRequestV1.model_validate(request)
    from personal_brain.research.deep_coordinator import _operation_id
    from personal_brain.research.deep_controller import query_fingerprint
    key = f"round:1:internal:{query_fingerprint('first record')}"
    gateway.operations[str(_operation_id(run_id, key))] = "outcome_unknown"

    result = coordinator._retrieve(
        job(run_id, user_id), "worker-1", parsed_request, "first record", round_number=1
    )

    assert result[2] == "outcome_unknown"
    assert retrieval.calls == []
    assert gateway.appended_rounds == []


@pytest.mark.parametrize(
    "operation_status,expected_code",
    [
        ("failed", "deep_operation_reauthorization_required"),
        ("cancelled", "deep_operation_reauthorization_required"),
        ("abandoned", "deep_operation_reauthorization_required"),
        ("outcome_unknown", "deep_operation_reauthorization_required"),
        ("running", "deep_operation_reauthorization_required"),
        ("completed", "missing_completed_plan"),
    ],
)
def test_r2b_terminal_or_inflight_operation_never_reenters_provider(
    operation_status: str, expected_code: str
) -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    gateway = FakeGateway(request, None, {})
    gateway.operations[str(_operation_id(run_id, "plan:v1"))] = operation_status
    planner = CountingPlanner()

    with pytest.raises(GatewayError) as caught:
        DeepResearchCoordinator(
            gateway, FakeRetrieval({}), planner=planner
        ).process_claimed(job(run_id, user_id), "takeover-worker", 120)

    assert caught.value.code == expected_code
    assert planner.calls == 0
    assert gateway.settlements == []


@pytest.mark.parametrize(
    "operation_status",
    ["failed", "cancelled", "abandoned", "outcome_unknown", "running"],
)
def test_r2c_terminal_or_inflight_operation_blocks_without_provider_call(
    operation_status: str,
) -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    plan = plan_json(request)
    gateway = FakeGateway(request, plan, {})
    gateway.bundle["state"] = assessing_state(run_id, plan)
    gateway.checkpoints = [deepcopy(gateway.bundle["state"])]
    gateway.operations[
        str(_operation_id(run_id, "round:1:evaluate"))
    ] = operation_status
    evaluator = CountingEvaluator()

    result = DeepResearchCoordinator(
        gateway, FakeRetrieval({}), evaluator=evaluator
    ).process_claimed(job(run_id, user_id), "takeover-worker", 120)

    assert result.status == "completed"
    assert evaluator.calls == 0
    assert gateway.checkpoints[-1]["stopReason"] == "authorization_blocked"
    assert gateway.settlements == []


def test_completed_r2c_settlement_is_replayed_without_duplicate_provider_call() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    request["effectiveLimits"]["maxQueries"] = 1
    plan = plan_json(request)
    gateway = FakeGateway(request, plan, {})
    gateway.bundle["state"] = assessing_state(run_id, plan)
    gateway.checkpoints = [deepcopy(gateway.bundle["state"])]
    assessment = ProgressiveEvaluator().assess(
        ResearchPlanV1.model_validate(plan), [], user_id=user_id
    ).assessment
    gateway.bundle["assessment"] = assessment.model_dump(mode="json")
    gateway.operations[
        str(_operation_id(run_id, "round:1:evaluate"))
    ] = "completed"
    evaluator = CountingEvaluator()

    result = DeepResearchCoordinator(
        gateway, FakeRetrieval({}), evaluator=evaluator
    ).process_claimed(job(run_id, user_id), "restart-worker", 120)

    assert result.status == "completed"
    assert evaluator.calls == 0
    assert gateway.settlements == []


def test_r2c_oversized_evidence_blocks_before_reservation_or_provider_call() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    plan = plan_json(request)
    rows = [
        evidence_row(uuid4(), uuid4(), uuid4(), f"source {index}")
        for index in range(81)
    ]
    gateway = FakeGateway(request, plan, {})
    gateway.bundle["state"] = assessing_state(run_id, plan)
    gateway.bundle["evidence"] = rows
    gateway.checkpoints = [deepcopy(gateway.bundle["state"])]

    class NeverCalledModel:
        provider = "fixture"
        model_name = "fixture"

        def max_operation_cost(self, _prompt):
            return Decimal("0.0500")

        def evaluate(self, _prompt):
            raise AssertionError("oversized evidence must not reach R2C")

        def repair(self, _prompt):
            raise AssertionError("oversized evidence must not reach R2C repair")

    result = DeepResearchCoordinator(
        gateway, FakeRetrieval({}), evaluator=EvidenceEvaluator(NeverCalledModel())
    ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert result.status == "completed"
    assert not any(row["cost_category"] == "r2c" for row in gateway.reservations)
    assert gateway.checkpoints[-1]["stopReason"] == "authorization_blocked"


def test_r2c_budget_preflight_blocks_before_provider_call() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    plan = plan_json(request)

    class BudgetRejectingGateway(FakeGateway):
        def reserve_deep_research_operation(self, _job, _worker, **kwargs):
            self.reservations.append(deepcopy(kwargs))
            if kwargs["cost_category"] == "r2c":
                return {
                    "operationId": kwargs["operation_id"],
                    "status": "budget_exhausted",
                    "reservedCostUsd": "0",
                }
            return super().reserve_deep_research_operation(_job, _worker, **kwargs)

    gateway = BudgetRejectingGateway(request, plan, {})
    gateway.bundle["state"] = assessing_state(run_id, plan)
    gateway.checkpoints = [deepcopy(gateway.bundle["state"])]
    evaluator = CountingEvaluator()

    result = DeepResearchCoordinator(
        gateway, FakeRetrieval({}), evaluator=evaluator
    ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert result.status == "completed"
    assert evaluator.calls == 0
    r2c_reservations = [
        row for row in gateway.reservations if row["cost_category"] == "r2c"
    ]
    assert r2c_reservations[0]["estimated_cost_usd"] == "0.0500"
    assert gateway.checkpoints[-1]["stopReason"] == "budget_exhausted"


def test_stale_worker_stops_before_any_side_effect() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    gateway = FakeGateway(request, plan_json(request), {})
    gateway.lease_valid = False
    retrieval = FakeRetrieval({})

    result = DeepResearchCoordinator(
        gateway, retrieval, evaluator=ProgressiveEvaluator()
    ).process_claimed(job(run_id, user_id), "stale-worker", 120)

    assert result.status == "lease_lost"
    assert gateway.checkpoints == []
    assert gateway.operations == {}


def test_deadline_before_planning_starts_no_operation() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    gateway = FakeGateway(request, None, {})
    gateway.bundle["deadlineAt"] = "2026-01-01T00:00:00+00:00"

    result = DeepResearchCoordinator(
        gateway, FakeRetrieval({}),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert result.status == "completed"
    assert gateway.operations == {}
    assert gateway.completed == {
        "outcome": "insufficient", "stop_reason": "duration_exhausted"
    }


def test_deadline_after_retrieval_stops_before_evaluation() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    gateway = FakeGateway(request, plan_json(request), {first["id"]: first})
    gateway.bundle["deadlineAt"] = "2026-01-01T00:00:01+00:00"
    moments = iter([
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
    ])
    evaluator = ProgressiveEvaluator()

    class NoPostDeadlineOrchestrator:
        def run(self, *_args, **_kwargs):
            raise AssertionError("post-deadline synthesis must not start")

    result = DeepResearchCoordinator(
        gateway, FakeRetrieval({"first record": [first]}), evaluator=evaluator,
        orchestrator=NoPostDeadlineOrchestrator(),
        clock=lambda: next(moments),
    ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert result.status == "completed"
    assert evaluator.calls == 0
    assert gateway.checkpoints[-1]["stopReason"] == "duration_exhausted"
    assert gateway.completed == {
        "outcome": "qualified_partial", "stop_reason": "duration_exhausted"
    }


def test_expired_deadline_is_reapplied_after_checkpoint_resume() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    gateway = FakeGateway(request, plan_json(request), {})
    gateway.bundle["deadlineAt"] = "2026-01-01T00:00:00+00:00"

    result = DeepResearchCoordinator(
        gateway, FakeRetrieval({}),
        clock=lambda: datetime(2026, 1, 2, tzinfo=timezone.utc),
    ).process_claimed(job(run_id, user_id), "worker-2", 120)

    assert result.status == "completed"
    assert gateway.operations == {}
    assert gateway.checkpoints[-1]["stopReason"] == "duration_exhausted"


def test_query_limit_stops_before_second_retrieval() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    request["effectiveLimits"]["maxQueries"] = 1
    first = evidence_row(uuid4(), uuid4(), uuid4(), "first source")
    gateway = FakeGateway(request, plan_json(request), {first["id"]: first})
    retrieval = FakeRetrieval({"first record": [first]})

    result = DeepResearchCoordinator(
        gateway, retrieval, evaluator=ProgressiveEvaluator()
    ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert result.status == "completed"
    assert retrieval.calls == ["first record"]
    assert gateway.checkpoints[-1]["stopReason"] == "max_queries_exhausted"


def test_r2b_and_r2c_reserve_before_call_and_settle_actual_cost() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    request["effectiveLimits"]["maxQueries"] = 1
    first = evidence_row(uuid4(), uuid4(), uuid4(), "direct source")
    gateway = FakeGateway(request, None, {first["id"]: first})

    class CostedPlanner:
        def max_operation_cost(self, _request, _context):
            return Decimal("0.0400")

        def plan(self, parsed_request, context, *, diagnostic_sink=None):
            result = DeepResearchPlanner().plan(parsed_request, context)
            if diagnostic_sink is not None:
                for diagnostic in result.diagnostics:
                    diagnostic_sink(diagnostic)
            return result.__class__(
                plan=result.plan, quality=result.quality,
                prompt_version=result.prompt_version,
                model_provider="fixture:fallback", model_name="fixture",
                repaired=True, fallback_used=True,
                actual_cost_usd=Decimal("0.0200"),
                usage=LLMUsage(100, 25, 125, reasoning_tokens=10),
                request_ids=("req-r2b",), finish_reasons=("stop",),
            )

    class CostedEvaluator(ProgressiveEvaluator):
        def max_operation_cost(self, _plan, _records, *, user_id):
            del user_id
            return Decimal("0.0500")

        def assess(self, plan, records, *, user_id, diagnostic_sink=None):
            result = super().assess(plan, records, user_id=user_id)
            if diagnostic_sink is not None:
                diagnostic_sink(R2CValidationDiagnostic(
                    attemptNumber=1, attemptStage="initial",
                    requestId="req-r2c", finishReason="stop",
                    inputTokens=200, outputTokens=50, reasoningTokens=20,
                    costUsd=Decimal("0.0300"), latencyMs=10,
                    validationStage="accepted",
                    diagnosticSummary="R2C fixture accepted",
                ))
            return EvaluationResult(
                result.assessment, repaired=False, fallback_used=False,
                actual_cost_usd=Decimal("0.0300"),
                usage=LLMUsage(200, 50, 250, reasoning_tokens=20),
                request_ids=("req-r2c",), finish_reasons=("stop",),
            )

    result = DeepResearchCoordinator(
        gateway,
        FakeRetrieval({"Compare the records": [first]}),
        planner=CostedPlanner(), evaluator=CostedEvaluator(),
    ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert result.status == "completed"
    paid_reservations = [row for row in gateway.reservations if row["cost_category"] in {"r2b", "r2c"}]
    assert [(row["cost_category"], row["estimated_cost_usd"]) for row in paid_reservations] == [
        ("r2b", "0.0400"), ("r2c", "0.0500"),
    ]
    paid_settlements = [row for row in gateway.settlements if row["actual_cost_usd"] != "0"]
    assert [row["actual_cost_usd"] for row in paid_settlements] == ["0.0200", "0.0300"]
    assert paid_settlements[0]["error_code"] == "r2b_fallback"
    assert len(gateway.validation_diagnostics) == 2
    assert gateway.validation_diagnostics[0]["diagnostic"]["attemptStage"] == "fallback"
    assert gateway.validation_diagnostics[1]["diagnostic"]["schemaVersion"] == "r2c-validation-diagnostic-v1"
    assert gateway.validation_diagnostics[1]["diagnostic"]["costUsd"] == "0.0300"
    assert "request" not in gateway.validation_diagnostics[0]["diagnostic"]
    planning_metadata = json.loads(paid_settlements[0]["result_ref"])
    evaluation_metadata = json.loads(paid_settlements[1]["result_ref"])
    assert planning_metadata == {
        "requestIds": ["req-r2b"], "finishReasons": ["stop"],
        "usage": {"inputTokens": 100, "outputTokens": 25, "totalTokens": 125,
                  "cachedInputTokens": 0, "cacheWriteInputTokens": 0,
                  "reasoningTokens": 10},
    }
    assert evaluation_metadata["requestIds"] == ["req-r2c"]
    assert evaluation_metadata["usage"]["reasoningTokens"] == 20


def test_deadline_crossed_during_r2b_settles_cost_but_discards_output() -> None:
    run_id, user_id = uuid4(), uuid4()
    request = request_json(run_id, user_id)
    gateway = FakeGateway(request, None, {})
    gateway.bundle["deadlineAt"] = "2026-01-01T00:00:01+00:00"

    class CostedPlanner:
        def max_operation_cost(self, _request, _context):
            return Decimal("0.0400")

        def plan(self, parsed_request, context, *, diagnostic_sink=None):
            result = DeepResearchPlanner().plan(parsed_request, context)
            if diagnostic_sink is not None:
                for diagnostic in result.diagnostics:
                    diagnostic_sink(diagnostic)
            return result.__class__(
                plan=result.plan, quality=result.quality,
                prompt_version=result.prompt_version,
                model_provider="fixture", model_name="fixture",
                repaired=False, fallback_used=False,
                actual_cost_usd=Decimal("0.0200"),
                request_ids=("req-r2b",),
            )

    moments = iter([
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
    ])
    result = DeepResearchCoordinator(
        gateway, FakeRetrieval({}), planner=CostedPlanner(),
        clock=lambda: next(moments),
    ).process_claimed(job(run_id, user_id), "worker-1", 120)

    assert result.status == "completed"
    assert gateway.bundle["plan"] is None
    assert gateway.settlements[-1]["actual_cost_usd"] == "0.0200"
    assert gateway.settlements[-1]["error_code"] == "duration_exhausted"
