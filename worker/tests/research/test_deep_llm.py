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
    bounded_repair_diagnostic,
)
from personal_brain.research.deep_planning import (
    DeepResearchPlanner,
    ProposedResearchPlan,
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


def assessment_output(plan, *, gap_score=None):
    gaps = [] if gap_score is None else [{
        "id": "gap_01",
        "requirementId": plan.evidenceRequirements[0].id,
        "severity": "critical",
        "expectedInformationGain": gap_score,
        "resolvability": 3,
        "dependencyImpact": gap_score,
        "proposedAction": "search_internal",
        "explanation": "Required evidence is missing",
    }]
    return {
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
        "independence": "unknown", "conflicts": [], "prioritizedGaps": gaps,
        "explanation": "Evidence is insufficient",
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


class UnexpectedCacheWriteClient(FakeClient):
    def generate_with_metadata(self, **_kwargs):
        return StructuredLLMResult(
            output=proposal(), provider=self.provider, model=self.model,
            usage=LLMUsage(100, 25, 125, 0, 100, 5),
            latency_ms=4, request_id="req-cache-write", service_tier="default",
        )


class UnexpectedServiceTierClient(FakeClient):
    service_tier = "default"

    def generate_with_metadata(self, **_kwargs):
        return StructuredLLMResult(
            output=proposal(), provider=self.provider, model=self.model,
            usage=LLMUsage(100, 25, 125), latency_ms=4,
            request_id="req-fast-tier", service_tier="priority",
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


def test_r2b_schema_contains_no_untyped_object_maps():
    schema = to_openai_schema(ProposedResearchPlan)

    def assert_strict_objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert isinstance(node.get("properties"), dict)
                assert node.get("required") == list(node["properties"])
                assert node.get("additionalProperties") is False
            for value in node.values():
                assert_strict_objects(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict_objects(value)

    assert_strict_objects(schema)


def test_provider_schema_preserves_numeric_bounds_as_model_instructions():
    schema = to_openai_schema(
        ProposedEvidenceAssessment, preserve_numeric_bounds=True
    )
    gap = schema["$defs"]["ResearchGapV1"]["properties"]
    assert "greater than or equal to 1" in gap["expectedInformationGain"]["description"]
    assert "less than or equal to 5" in gap["expectedInformationGain"]["description"]
    assert gap["expectedInformationGain"]["minimum"] == 1
    assert gap["expectedInformationGain"]["maximum"] == 5


def test_legacy_schema_mode_still_removes_numeric_bounds():
    schema = to_openai_schema(ProposedEvidenceAssessment)
    score = schema["$defs"]["ResearchGapV1"]["properties"]["expectedInformationGain"]
    assert "minimum" not in score
    assert "maximum" not in score


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
    output = assessment_output(plan)
    model = OpenAICompatibleEvidenceEvaluationModel(FakeClient([output]), pricing())
    result = EvidenceEvaluator(model).assess(plan, [], user_id=UUID(USER_ID))
    assert result.fallback_used is False
    assert result.assessment.telemetry.executionStatus == "provider_success"
    assert result.assessment.telemetry.providerRequestIds == ["req-1"]
    assert result.actual_cost_usd == Decimal("0.0002")


def test_r2c_out_of_range_scores_are_not_coerced_and_repair_preserves_metadata():
    plan = DeepResearchPlanner().plan(
        request(), build_planning_context_pack([], request())
    ).plan
    invalid = assessment_output(plan, gap_score=10)
    repaired = assessment_output(plan, gap_score=5)
    client = FakeClient([invalid, repaired])
    model = OpenAICompatibleEvidenceEvaluationModel(client, pricing())

    result = EvidenceEvaluator(model).assess(plan, [], user_id=UUID(USER_ID))

    assert result.repaired is True
    assert result.fallback_used is False
    assert result.assessment.prioritizedGaps[0].expectedInformationGain == 5
    assert result.usage == LLMUsage(200, 50, 250)
    assert result.actual_cost_usd == Decimal("0.0004")
    assert result.request_ids == ("req-1", "req-2")
    score_schema = client.schemas[0]["$defs"]["ResearchGapV1"]["properties"]
    assert score_schema["expectedInformationGain"]["maximum"] == 5


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


def test_unexpected_cache_write_is_uncertain_not_underpriced():
    model = OpenAICompatibleDeepPlanningModel(UnexpectedCacheWriteClient([]), pricing())
    with pytest.raises(DeepProviderError) as caught:
        DeepResearchPlanner(model).plan(
            request(), build_planning_context_pack([], request())
        )
    assert caught.value.outcome_unknown is True
    assert "prompt caching" in str(caught.value)


def test_unexpected_service_tier_is_uncertain_not_underpriced():
    model = OpenAICompatibleDeepPlanningModel(UnexpectedServiceTierClient([]), pricing())
    with pytest.raises(DeepProviderError) as caught:
        DeepResearchPlanner(model).plan(
            request(), build_planning_context_pack([], request())
        )
    assert caught.value.outcome_unknown is True
    assert caught.value.error_kind == "unexpected_service_tier"
    assert caught.value.known_cost_usd == Decimal("0")


def test_repair_diagnostic_is_printable_and_bounded_for_cost_reservation():
    diagnostic = bounded_repair_diagnostic(ValueError("é\n" * 2000))
    assert len(diagnostic) <= 1000
    assert diagnostic.isascii()
    assert "\n" not in diagnostic


def test_environment_configuration_fails_closed_without_explicit_model_and_pricing():
    with patch.dict("os.environ", {
        "R2B_R2C_API_BASE_URL": "https://llm.example.test/v1",
        "R2B_R2C_API_KEY": "secret",
    }, clear=True):
        with pytest.raises(ValueError):
            OpenAICompatibleDeepPlanningModel.from_environment()


def test_deep_environment_uses_bounded_standard_reasoning_configuration():
    with patch.dict("os.environ", {
        "R2B_R2C_API_BASE_URL": "https://api.openai.com/v1",
        "R2B_R2C_API_KEY": "secret",
        "R2B_R2C_PROVIDER": "openai",
        "R2B_R2C_SERVICE_TIER": "default",
        "R2B_R2C_PROMPT_CACHE_MODE": "explicit",
        "R2B_MODEL": "gpt-6.1-sol",
        "R2B_MAX_OUTPUT_TOKENS": "1024",
        "R2B_TIMEOUT_SECONDS": "90",
        "R2B_REASONING_EFFORT": "low",
        "R2B_PRICING_VERIFIED": "true",
        "R2B_INPUT_COST_PER_MILLION_USD": "2",
        "R2B_OUTPUT_COST_PER_MILLION_USD": "10",
    }, clear=True):
        model = OpenAICompatibleDeepPlanningModel.from_environment()
    assert model.client.base_url == "https://api.openai.com/v1"
    assert model.client.provider == "openai"
    assert model.client.model == "gpt-6.1-sol"
    assert model.client.max_output_tokens == 1024
    assert model.client.timeout_seconds == 90
    assert model.client.reasoning_effort == "low"
    assert model.client.service_tier == "default"
    assert model.client.prompt_cache_mode == "explicit"
