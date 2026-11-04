from decimal import Decimal
from uuid import UUID
from unittest.mock import patch

import pytest

from personal_brain.llm_client import LLMUsage, StructuredLLMError, StructuredLLMResult
from personal_brain.research.deep_evaluation import EvidenceEvaluator, evaluation_prompt
from personal_brain.research.deep_llm import (
    OpenAICompatibleDeepPlanningModel,
    OpenAICompatibleEvidenceEvaluationModel,
    VerifiedPricing,
    DeepProviderError,
)
from personal_brain.research.deep_planning import (
    DeepResearchPlanner,
    build_planning_context_pack,
)
from personal_brain.research.schema_utils import to_openai_schema
from personal_brain.research.deep_evaluation import ProposedEvidenceAssessment
from personal_brain.research.deep_contracts import ResearchRequestV1


USER_ID = "31000000-0000-4000-8000-000000000001"
RUN_ID = "31000000-0000-4000-8000-000000000002"


def request():
    return ResearchRequestV1.model_validate({
        "schemaVersion": "research-request-v1",
        "requestId": "31000000-0000-4000-8000-000000000003",
        "runId": RUN_ID,
        "idempotencyKey": "31000000-0000-4000-8000-000000000004",
        "userId": USER_ID,
        "objective": "Summarize the supplied evidence",
        "originalQuestion": "What does the evidence establish?",
        "executionMode": "deep",
        "internalKnowledgeScope": {"selectedAssetIds": None},
        "webPolicy": "disabled",
        "permissions": {"internalKnowledge": True, "webDiscovery": False, "webFetch": False},
        "effectiveLimits": {"maxRounds": 2, "maxQueries": 2, "maxCostUsd": "0.5", "maxDurationSeconds": 300},
        "analysisSpec": None,
    })


def proposal():
    return {
        "normalizedResearchIntent": "Summarize supplied evidence",
        "objective": "Summarize the supplied evidence",
        "explicitConstraints": ["Internal evidence only"],
        "inferredAssumptions": [],
        "essentialClarifications": [],
        "subquestions": [{
            "key": "evidence", "question": "What does the evidence establish?",
            "priority": 1, "dependsOnKeys": [],
            "requirements": [{"description": "Direct evidence", "required": True, "evidenceType": "primary_source"}],
            "internalQueries": ["direct evidence"], "webQueries": [],
        }],
    }


class FakeClient:
    provider = "fixture-provider"
    model = "fixture-model"
    max_output_tokens = 512

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.schemas = []

    def generate_with_metadata(self, *, output_schema, **_kwargs):
        self.schemas.append(output_schema)
        output = self.outputs.pop(0)
        return StructuredLLMResult(
            output=output, provider=self.provider, model=self.model,
            usage=LLMUsage(100, 25, 125), latency_ms=9,
            request_id=f"req-{len(self.schemas)}",
        )


class UnknownOutcomeClient(FakeClient):
    def generate_with_metadata(self, **_kwargs):
        raise StructuredLLMError("timeout", outcome_unknown=True)


class MissingUsageClient(FakeClient):
    def generate_with_metadata(self, **_kwargs):
        return StructuredLLMResult(
            output=proposal(), provider=self.provider, model=self.model,
            usage=LLMUsage(), latency_ms=4, request_id="req-no-usage",
        )


def pricing():
    return VerifiedPricing(Decimal("1.0"), Decimal("2.0"))


def test_r2b_adapter_repairs_once_and_aggregates_usage_and_cost():
    model = OpenAICompatibleDeepPlanningModel(FakeClient([{"bad": True}, proposal()]), pricing())
    result = DeepResearchPlanner(model).plan(
        request(), build_planning_context_pack([], request())
    )
    assert result.repaired is True
    assert result.fallback_used is False
    assert result.usage == LLMUsage(200, 50, 250)
    assert result.actual_cost_usd == Decimal("0.0004")
    assert result.request_ids == ("req-1", "req-2")


def test_r2c_schema_excludes_backend_owned_identity_and_telemetry():
    schema = to_openai_schema(ProposedEvidenceAssessment)
    properties = schema["properties"]
    assert "assessmentId" not in properties
    assert "runId" not in properties
    assert "planId" not in properties
    assert "telemetry" not in properties
    assert "maxItems" not in str(schema)


def test_max_reservation_covers_initial_and_repair_calls():
    model = OpenAICompatibleDeepPlanningModel(FakeClient([proposal()]), pricing())
    prompt = {
        "systemInstructions": "bounded",
        "researchContext": {"question": "test"},
        "outputSchema": {},
    }
    assert model.max_operation_cost(prompt) >= pricing().cost(LLMUsage(1, 1024))


def test_r2c_adapter_persists_provider_success_metadata():
    plan = DeepResearchPlanner().plan(
        request(), build_planning_context_pack([], request())
    ).plan
    output = {
        "evidenceQuality": [],
        "requirementCoverage": [{
            "requirementId": plan.evidenceRequirements[0].id,
            "status": "missing", "sufficiency": "fail", "independence": "unknown",
            "supportingEvidenceIds": [], "contradictingEvidenceIds": [],
            "explanation": "No evidence was supplied",
        }],
        "subquestionStatus": [{
            "subquestionId": plan.subquestions[0].id, "status": "missing",
            "requirementIds": plan.subquestions[0].evidenceRequirementIds,
        }],
        "overallStatus": "insufficient", "contradiction": "unknown",
        "independence": "unknown", "conflicts": [], "prioritizedGaps": [],
        "explanation": "Evidence is insufficient",
    }
    model = OpenAICompatibleEvidenceEvaluationModel(FakeClient([output]), pricing())
    result = EvidenceEvaluator(model).assess(plan, [], user_id=UUID(USER_ID))
    assert result.fallback_used is False
    assert result.assessment.telemetry.executionStatus == "provider_success"
    assert result.assessment.telemetry.providerRequestIds == ["req-1"]
    assert result.actual_cost_usd == Decimal("0.0002")


def test_unknown_provider_outcome_never_becomes_silent_fallback():
    model = OpenAICompatibleDeepPlanningModel(UnknownOutcomeClient([]), pricing())
    with pytest.raises(DeepProviderError) as caught:
        DeepResearchPlanner(model).plan(
            request(), build_planning_context_pack([], request())
        )
    assert caught.value.outcome_unknown is True


def test_missing_authoritative_usage_is_uncertain_not_zero_cost_success():
    model = OpenAICompatibleDeepPlanningModel(MissingUsageClient([]), pricing())
    with pytest.raises(DeepProviderError) as caught:
        DeepResearchPlanner(model).plan(
            request(), build_planning_context_pack([], request())
        )
    assert caught.value.outcome_unknown is True
    assert "usage" in str(caught.value)


def test_environment_configuration_fails_closed_without_explicit_model_and_pricing():
    with patch.dict("os.environ", {
        "R2B_R2C_API_BASE_URL": "https://llm.example.test/v1",
        "R2B_R2C_API_KEY": "secret",
    }, clear=True):
        with pytest.raises(ValueError):
            OpenAICompatibleDeepPlanningModel.from_environment()
