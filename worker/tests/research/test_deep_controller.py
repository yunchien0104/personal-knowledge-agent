from personal_brain.research.deep_contracts import (
    EvidenceAssessmentV1,
    ResearchIterationStateV1,
    ResearchPlanV1,
    ResearchRequestV1,
)
from personal_brain.research.deep_controller import (
    ResearchIterationController,
    material_coverage_gain,
    query_fingerprint,
)
from worker.tests.research.test_deep_contracts import (
    assessment_data,
    iteration_data,
    plan_data,
    request_data,
)


def contracts():
    request = ResearchRequestV1.model_validate(request_data())
    plan = ResearchPlanV1.model_validate(plan_data()).validate_against_request(request)
    assessment = EvidenceAssessmentV1.model_validate(assessment_data())
    state = ResearchIterationStateV1.model_validate(iteration_data(
        executionPhase="deciding",
        checkpointVersion=2,
        currentRound=1,
    ))
    return request, plan, assessment, state


def test_internal_first_and_query_deduplication():
    request, plan, assessment, state = contracts()
    decision = ResearchIterationController().decide(request, plan, assessment, state)
    assert decision.action.actionType == "search_internal"
    first = decision.action.query
    state.queryFingerprints.append(query_fingerprint(first))
    next_decision = ResearchIterationController().decide(request, plan, assessment, state)
    assert next_decision.action.query != first


def test_quality_hard_limits_and_diminishing_returns_stop_truthfully():
    request, plan, assessment, state = contracts()
    sufficient = assessment.model_copy(update={
        "overallStatus": "sufficient",
        "requirementCoverage": [item.model_copy(update={
            "status": "satisfied", "sufficiency": "pass"
        }) for item in assessment.requirementCoverage],
    })
    assert ResearchIterationController().decide(request, plan, sufficient, state).stop_reason == "quality_reached"
    maxed = state.model_copy(update={"currentRound": request.effectiveLimits.maxRounds})
    assert ResearchIterationController().decide(request, plan, assessment, maxed).stop_reason == "max_rounds_reached"
    stale = state.model_copy(update={"consecutiveNoInformationGainRounds": 2})
    assert ResearchIterationController().decide(request, plan, assessment, stale).stop_reason == "diminishing_returns"


def test_information_gain_tracks_coverage_not_document_count():
    before = {"req_primary": "missing", "req_conflict": "partial"}
    assert material_coverage_gain(before, {**before, "unrelated": "satisfied"}) is False
    assert material_coverage_gain(before, {"req_primary": "partial", "req_conflict": "partial"}) is True
