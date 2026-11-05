from decimal import Decimal
from uuid import UUID

from personal_brain.research.deep_contracts import (
    EvidenceAssessmentV1,
    ResearchBudgetStateV1,
    ResearchIterationStateV1,
    ResearchRequestV1,
)
from personal_brain.research.deep_controller import ResearchIterationController
from personal_brain.research.deep_evaluation import EvidenceEvaluator
from personal_brain.research.deep_planning import DeepResearchPlanner, build_planning_context_pack
from worker.tests.research.test_deep_evaluation import (
    RUN_ID,
    USER_ID,
    FixtureModel,
    assessment_output,
    evidence,
)


def quality_contracts():
    request = ResearchRequestV1.model_validate({
        "schemaVersion": "research-request-v1",
        "requestId": "30000000-0000-4000-8000-000000000003",
        "runId": RUN_ID,
        "idempotencyKey": "30000000-0000-4000-8000-000000000004",
        "userId": USER_ID,
        "objective": "Compare the evidence and identify unresolved conflicts",
        "originalQuestion": "Compare the evidence and identify unresolved conflicts",
        "executionMode": "deep",
        "internalKnowledgeScope": {"selectedAssetIds": None},
        "webPolicy": "disabled",
        "permissions": {"internalKnowledge": True, "webDiscovery": False, "webFetch": False},
        "effectiveLimits": {"maxRounds": 3, "maxQueries": 8, "maxCostUsd": "1.0", "maxDurationSeconds": 1800},
        "analysisSpec": None,
    })
    plan = DeepResearchPlanner().plan(
        request, build_planning_context_pack([], request)
    ).plan
    state = ResearchIterationStateV1(
        schemaVersion="research-iteration-state-v1",
        runId=plan.runId,
        planVersion=plan.planVersion,
        checkpointVersion=2,
        currentRound=1,
        completedRounds=[1],
        executionPhase="deciding",
        queryFingerprints=[],
        operationIds=[],
        assessmentIds=[],
        requirementProgress={item.id: "unknown" for item in plan.evidenceRequirements},
        unresolvedCriticalGapIds=[],
        proposedActions=[],
        approvedActionIds=[],
        budget=ResearchBudgetStateV1(
            limitUsd=Decimal("1.0"), protectedFinalReportReserveUsd=Decimal("0.1"),
            reservedUsd=Decimal("0"), actualUsd=Decimal("0"), uncertainUsd=Decimal("0"),
        ),
        consecutiveNoInformationGainRounds=0,
    )
    return request, plan, state


def evaluate(output, records):
    _, plan, _ = quality_contracts()
    return EvidenceEvaluator(FixtureModel(output)).assess(
        plan, records, user_id=UUID(USER_ID)
    )


def decision(result):
    request, plan, state = quality_contracts()
    return ResearchIterationController().decide(request, plan, result.assessment, state)


def test_sufficient_evidence_supporting_conclusion_is_answerable():
    _, plan, _ = quality_contracts()
    record = evidence("30000000-0000-4000-8000-000000000020")
    output = assessment_output(plan, [str(record.evidenceId)])
    output["explanation"] = "The evidence is sufficient and supports the requested conclusion."
    result = evaluate(output, [record])
    assert result.assessment.overallStatus == "sufficient"
    assert decision(result).stop_reason == "quality_reached"


def test_sufficient_evidence_refuting_proposition_is_still_answerable():
    _, plan, _ = quality_contracts()
    record = evidence("30000000-0000-4000-8000-000000000021", content_hash="b" * 64)
    output = assessment_output(plan, [])
    for coverage in output["requirementCoverage"]:
        coverage["contradictingEvidenceIds"] = [str(record.evidenceId)]
        coverage["explanation"] = "Sufficient evidence answers the question negatively."
    output["evidenceQuality"] = [{
        "evidenceId": str(record.evidenceId), "relevance": "pass",
        "reliability": "pass", "freshness": "pass",
        "explanation": "Direct evidence refutes the proposition",
    }]
    output["explanation"] = "The proposition is not supported; that negative answer is sufficiently established."
    result = evaluate(output, [record])
    assert result.assessment.overallStatus == "sufficient"
    assert result.assessment.requirementCoverage[0].contradictingEvidenceIds
    assert decision(result).outcome == "complete"


def test_insufficient_evidence_does_not_reach_quality_gate():
    _, plan, _ = quality_contracts()
    output = assessment_output(plan, [], overall="insufficient")
    for coverage in output["requirementCoverage"]:
        coverage.update({"status": "missing", "sufficiency": "fail"})
    for subquestion in output["subquestionStatus"]:
        subquestion["status"] = "missing"
    output["prioritizedGaps"] = [{
        "id": "gap_critical", "requirementId": plan.evidenceRequirements[0].id,
        "severity": "critical", "expectedInformationGain": 5, "resolvability": 5,
        "dependencyImpact": 5, "proposedAction": "search_internal",
        "explanation": "Required evidence is missing",
    }]
    result = evaluate(output, [])
    assert result.assessment.overallStatus == "insufficient"
    assert decision(result).stop_reason != "quality_reached"


def test_materially_contradictory_evidence_remains_unresolved():
    _, plan, _ = quality_contracts()
    first = evidence("30000000-0000-4000-8000-000000000022", content_hash="c" * 64)
    second = evidence("30000000-0000-4000-8000-000000000023", content_hash="d" * 64)
    output = assessment_output(plan, [str(first.evidenceId)], overall="conflicted")
    for coverage in output["requirementCoverage"]:
        coverage.update({"status": "conflicted", "sufficiency": "fail",
                         "contradictingEvidenceIds": [str(second.evidenceId)]})
    for subquestion in output["subquestionStatus"]:
        subquestion["status"] = "conflicted"
    output["contradiction"] = "fail"
    output["conflicts"] = [{
        "id": "conflict_01", "classification": "contradiction",
        "evidenceIds": [str(first.evidenceId), str(second.evidenceId)],
        "explanation": "The same claim under the same conditions has incompatible results",
    }]
    result = evaluate(output, [first, second])
    assert result.assessment.overallStatus == "conflicted"
    assert decision(result).stop_reason != "quality_reached"


def test_condition_difference_is_not_misread_as_material_contradiction():
    _, plan, _ = quality_contracts()
    first = evidence("30000000-0000-4000-8000-000000000024", content_hash="e" * 64)
    second = evidence("30000000-0000-4000-8000-000000000025", content_hash="f" * 64)
    output = assessment_output(plan, [str(first.evidenceId), str(second.evidenceId)])
    output["conflicts"] = [{
        "id": "difference_01", "classification": "condition_difference",
        "evidenceIds": [str(first.evidenceId), str(second.evidenceId)],
        "explanation": "Results differ because the tested conditions differ",
    }]
    result = evaluate(output, [first, second])
    assert result.assessment.overallStatus == "sufficient"
    assert decision(result).stop_reason == "quality_reached"


def test_non_independent_sources_preserve_concern_and_do_not_fake_sufficiency():
    _, plan, _ = quality_contracts()
    parent = "30000000-0000-4000-8000-000000000099"
    first = evidence("30000000-0000-4000-8000-000000000026", content_hash="1" * 64, parents=[parent])
    second = evidence("30000000-0000-4000-8000-000000000027", content_hash="2" * 64, parents=[parent])
    output = assessment_output(plan, [str(first.evidenceId), str(second.evidenceId)], overall="partial")
    for coverage in output["requirementCoverage"]:
        coverage.update({"status": "partial", "sufficiency": "concern", "independence": "pass"})
    for subquestion in output["subquestionStatus"]:
        subquestion["status"] = "partial"
    output["independence"] = "concern"
    result = evaluate(output, [first, second])
    assert all(item.independence == "concern" for item in result.assessment.requirementCoverage)
    assert result.assessment.overallStatus == "partial"
    assert decision(result).stop_reason != "quality_reached"
