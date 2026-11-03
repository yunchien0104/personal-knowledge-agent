from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID, uuid4

from personal_brain.models import JobPayloadV1
from personal_brain.research.deep_contracts import EvidenceAssessmentV1, ResearchPlanV1
from personal_brain.research.deep_coordinator import DeepResearchCoordinator
from personal_brain.research.deep_evaluation import EvaluationResult
from personal_brain.supabase_gateway import ClaimedJob


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
                       "budget": None, "evidence": [], "operations": []}
        self.catalog = catalog
        self.operations: dict[str, str] = {}
        self.checkpoints: list[dict] = []
        self.appended_rounds: list[int] = []
        self.completed: dict | None = None
        self.heartbeats = 0
        self.lease_valid = True

    def heartbeat(self, *_args) -> bool:
        self.heartbeats += 1
        return self.lease_valid

    def load_deep_research_state(self, *_args):
        return deepcopy(self.bundle)

    def reserve_deep_research_operation(self, _job, _worker, **kwargs):
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

    def settle_deep_research_operation(self, _job, _worker, **kwargs):
        self.operations[kwargs["operation_id"]] = kwargs["status"]
        return {"operationId": kwargs["operation_id"], "status": kwargs["status"]}

    def append_deep_research_evidence(self, _job, _worker, **kwargs):
        self.appended_rounds.append(kwargs["round_number"])
        known = {row["id"] for row in self.bundle["evidence"]}
        for row in kwargs["evidence"]:
            if row["id"] not in known:
                self.bundle["evidence"].append(deepcopy(self.catalog[row["id"]]))
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

    def assess(self, plan, records, *, user_id):
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
        return EvaluationResult(assessment, repaired=False, fallback_used=False)


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
