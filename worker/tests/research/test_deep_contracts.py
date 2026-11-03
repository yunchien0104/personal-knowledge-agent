from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from personal_brain.research.deep_contracts import (
    EvidenceAssessmentV1,
    EvidenceRecordV1,
    ResearchIterationStateV1,
    ResearchPlanV1,
    ResearchRequestV1,
    validate_iteration_transition,
)
from personal_brain.research_agent import validate_plan as validate_legacy_plan
from personal_brain.source_notes import ModelError


IDS = {
    "request": "10000000-0000-4000-8000-000000000001",
    "run": "10000000-0000-4000-8000-000000000002",
    "user": "10000000-0000-4000-8000-000000000003",
    "plan": "10000000-0000-4000-8000-000000000004",
    "assessment": "10000000-0000-4000-8000-000000000005",
    "evidence": "10000000-0000-4000-8000-000000000006",
    "asset": "10000000-0000-4000-8000-000000000007",
    "revision": "10000000-0000-4000-8000-000000000008",
    "action": "10000000-0000-4000-8000-000000000009",
}


def request_data(**changes):
    data = {
        "schemaVersion": "research-request-v1",
        "requestId": IDS["request"],
        "runId": IDS["run"],
        "idempotencyKey": "10000000-0000-4000-8000-000000000010",
        "userId": IDS["user"],
        "objective": "Compare the evidence and identify unresolved conflicts",
        "originalQuestion": "Compare the evidence and identify unresolved conflicts",
        "executionMode": "deep",
        "internalKnowledgeScope": {"selectedAssetIds": [IDS["asset"]]},
        "webPolicy": "disabled",
        "permissions": {
            "internalKnowledge": True,
            "webDiscovery": False,
            "webFetch": False,
        },
        "effectiveLimits": {
            "maxRounds": 3,
            "maxQueries": 8,
            "maxCostUsd": "0.5000",
            "maxDurationSeconds": 1800,
        },
        "analysisSpec": None,
    }
    data.update(changes)
    return data


def plan_data(**changes):
    data = {
        "schemaVersion": "research-plan-v1",
        "planId": IDS["plan"],
        "runId": IDS["run"],
        "requestId": IDS["request"],
        "planVersion": 1,
        "normalizedResearchIntent": "Compare evidence and resolve conflicts",
        "objective": "Compare the evidence and identify unresolved conflicts",
        "explicitConstraints": [],
        "inferredAssumptions": [],
        "essentialClarifications": [],
        "subquestions": [
            {
                "id": "sq_first",
                "question": "What does the primary internal evidence establish?",
                "priority": 1,
                "dependsOn": [],
                "evidenceRequirementIds": ["req_primary"],
            },
            {
                "id": "sq_conflict",
                "question": "Which material claims conflict with that evidence?",
                "priority": 2,
                "dependsOn": ["sq_first"],
                "evidenceRequirementIds": ["req_conflict"],
            },
        ],
        "evidenceRequirements": [
            {
                "id": "req_primary",
                "subquestionId": "sq_first",
                "description": "Primary source evidence",
                "required": True,
                "evidenceType": "primary_source",
            },
            {
                "id": "req_conflict",
                "subquestionId": "sq_conflict",
                "description": "Material counter-evidence",
                "required": True,
                "evidenceType": "counter_evidence",
            },
        ],
        "coverageCriteria": [
            {
                "id": "coverage_required",
                "description": "Both required evidence classes are addressed",
                "evidenceRequirementIds": ["req_primary", "req_conflict"],
            }
        ],
        "retrievalProposals": [
            {
                "id": "query_primary",
                "subquestionId": "sq_first",
                "source": "internal",
                "query": "primary evidence",
                "purpose": "Find the authoritative internal source",
            },
            {
                "id": "query_conflict",
                "subquestionId": "sq_conflict",
                "source": "internal",
                "query": "conflicting evidence",
                "purpose": "Find counter-evidence",
            },
        ],
        "revisionParentPlanId": None,
        "revisionReason": None,
    }
    data.update(changes)
    return data


def assessment_data(**changes):
    data = {
        "schemaVersion": "evidence-assessment-v1",
        "assessmentId": IDS["assessment"],
        "runId": IDS["run"],
        "planId": IDS["plan"],
        "planVersion": 1,
        "evidenceQuality": [],
        "requirementCoverage": [
            {
                "requirementId": "req_primary",
                "status": "missing",
                "sufficiency": "fail",
                "independence": "unknown",
                "supportingEvidenceIds": [],
                "contradictingEvidenceIds": [],
                "explanation": "No primary evidence was retrieved",
            }
        ],
        "subquestionStatus": [
            {
                "subquestionId": "sq_first",
                "status": "missing",
                "requirementIds": ["req_primary"],
            }
        ],
        "overallStatus": "insufficient",
        "contradiction": "unknown",
        "independence": "unknown",
        "conflicts": [],
        "prioritizedGaps": [],
        "explanation": "Required primary evidence is missing",
        "telemetry": {
            "modelProvider": "local",
            "modelName": "fixture",
            "promptVersion": "r2c-v1",
            "inputTokens": 0,
            "outputTokens": 0,
            "latencyMs": 1,
            "costUsd": "0",
        },
    }
    data.update(changes)
    return data


def iteration_data(**changes):
    data = {
        "schemaVersion": "research-iteration-state-v1",
        "runId": IDS["run"],
        "planVersion": 1,
        "checkpointVersion": 1,
        "currentRound": 0,
        "completedRounds": [],
        "executionPhase": "planning",
        "queryFingerprints": [],
        "operationIds": [],
        "assessmentIds": [],
        "requirementProgress": {"req_primary": "missing"},
        "unresolvedCriticalGapIds": [],
        "proposedActions": [],
        "approvedActionIds": [],
        "budget": {
            "limitUsd": "0.5000",
            "protectedFinalReportReserveUsd": "0.1000",
            "reservedUsd": "0",
            "actualUsd": "0",
            "uncertainUsd": "0",
        },
        "consecutiveNoInformationGainRounds": 0,
        "stopReason": None,
        "outcome": None,
    }
    data.update(changes)
    return data


def test_request_permissions_fail_closed():
    with pytest.raises(ValidationError):
        ResearchRequestV1.model_validate(request_data(
            webPolicy="auto",
            permissions={"internalKnowledge": True, "webDiscovery": True, "webFetch": False},
        ))


def test_plan_validates_references_cycles_objective_and_permissions():
    request = ResearchRequestV1.model_validate(request_data())
    ResearchPlanV1.model_validate(plan_data()).validate_against_request(request)

    cyclic = plan_data()
    cyclic["subquestions"][0]["dependsOn"] = ["sq_conflict"]
    with pytest.raises(ValidationError, match="cycle"):
        ResearchPlanV1.model_validate(cyclic)

    web_plan = plan_data()
    web_plan["retrievalProposals"][0]["source"] = "web"
    with pytest.raises(ValueError, match="unauthorized"):
        ResearchPlanV1.model_validate(web_plan).validate_against_request(request)

    changed_objective = plan_data(objective="A different objective")
    with pytest.raises(ValueError, match="preserve"):
        ResearchPlanV1.model_validate(changed_objective).validate_against_request(request)


def test_evidence_record_preserves_internal_provenance_and_rejects_snippet_citation():
    base = {
        "schemaVersion": "evidence-record-v1",
        "evidenceId": IDS["evidence"],
        "runId": IDS["run"],
        "userId": IDS["user"],
        "origin": "internal",
        "sourceIdentity": "asset revision evidence",
        "sourceMetadata": {},
        "provenance": {
            "assetId": IDS["asset"],
            "revisionId": IDS["revision"],
            "parentEvidenceIds": [],
            "captureMethod": "r1",
        },
        "excerpt": "A source-grounded excerpt",
        "citationLocator": {"kind": "pdf_page", "page_number": 2},
        "contentHash": "a" * 64,
        "capturedAt": datetime.now(UTC),
        "sourcePublishedAt": None,
        "storageScope": "knowledge_base",
        "citationEligible": True,
        "validityStatus": "valid",
        "isSearchSnippet": False,
    }
    EvidenceRecordV1.model_validate(base)
    with pytest.raises(ValidationError, match="snippets"):
        EvidenceRecordV1.model_validate({**base, "isSearchSnippet": True})


def test_missing_requirement_cannot_be_overridden_by_overall_sufficient():
    with pytest.raises(ValidationError, match="every assessed requirement"):
        EvidenceAssessmentV1.model_validate(assessment_data(overallStatus="sufficient"))


def test_iteration_transition_is_explicit_and_version_fenced():
    previous = ResearchIterationStateV1.model_validate(iteration_data())
    next_state = ResearchIterationStateV1.model_validate(iteration_data(
        checkpointVersion=2,
        executionPhase="retrieving",
        currentRound=1,
    ))
    assert validate_iteration_transition(previous, next_state) == next_state
    with pytest.raises(ValueError, match="checkpoint"):
        validate_iteration_transition(previous, next_state.model_copy(update={"checkpointVersion": 3}))


def test_deep_plan_cannot_enter_legacy_validator():
    with pytest.raises(ModelError):
        validate_legacy_plan(plan_data(), "Compare the evidence and identify unresolved conflicts")
