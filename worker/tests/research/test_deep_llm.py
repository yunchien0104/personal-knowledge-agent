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
    ProposedAssumption,
    ProposedRequirement,
    ProposedResearchPlan,
    build_planning_context_pack,
)
from personal_brain.research.schema_utils import to_openai_schema
from personal_brain.research.deep_evaluation import ProposedEvidenceAssessment
from personal_brain.research.deep_contracts import (
    EvidenceAssessmentV1,
    EvidenceRequirementV1,
    ResearchAssumptionV1,
    ResearchRequestV1,
)


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
            "queries": [{"source": "internal", "query": "direct evidence"}],
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
            request_id=f"req-{len(self.schemas)}", finish_reason="stop",
        )


class UnknownOutcomeClient(FakeClient):
    def generate_with_metadata(self, **_kwargs):
        raise StructuredLLMError("timeout", outcome_unknown=True)


class OutputBudgetExhaustedClient(FakeClient):
    def __init__(self):
        super().__init__([])
        self.calls = 0

    def generate_with_metadata(self, **_kwargs):
        self.calls += 1
        raise StructuredLLMError(
            "output exhausted",
            error_kind="output_budget_exhausted",
            outcome_unknown=False,
            usage=LLMUsage(1054, 950, 2004, reasoning_tokens=950),
            request_id="req-length",
            finish_reason="length",
        )


class TerminalProviderClient(FakeClient):
    def __init__(self, error_kind, finish_reason=None):
        super().__init__([])
        self.error_kind = error_kind
        self.finish_reason = finish_reason
        self.calls = 0

    def generate_with_metadata(self, **_kwargs):
        self.calls += 1
        raise StructuredLLMError(
            self.error_kind,
            error_kind=self.error_kind,
            outcome_unknown=False,
            usage=LLMUsage(100, 25, 125, reasoning_tokens=5),
            request_id=f"req-{self.error_kind}",
            finish_reason=self.finish_reason,
        )


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
    assert result.finish_reasons == ("stop", "stop")


def test_output_budget_exhaustion_never_uses_bounded_repair():
    client = OutputBudgetExhaustedClient()
    model = OpenAICompatibleDeepPlanningModel(client, pricing())
    with pytest.raises(DeepProviderError) as caught:
        DeepResearchPlanner(model).plan(
            request(), build_planning_context_pack([], request())
        )
    assert client.calls == 1
    assert caught.value.error_kind == "output_budget_exhausted"
    assert caught.value.finish_reasons == ("length",)
    assert caught.value.usage.reasoning_tokens == 950


@pytest.mark.parametrize(
    "error_kind,finish_reason",
    [
        ("provider_refusal", "stop"),
        ("content_filter", "content_filter"),
        ("invalid_json_response", "stop"),
    ],
)
def test_terminal_provider_errors_never_consume_repair_call(
    error_kind, finish_reason
):
    client = TerminalProviderClient(error_kind, finish_reason)
    model = OpenAICompatibleDeepPlanningModel(client, pricing())

    with pytest.raises(DeepProviderError) as caught:
        DeepResearchPlanner(model).plan(
            request(), build_planning_context_pack([], request())
        )

    assert client.calls == 1
    assert caught.value.error_kind == error_kind
    assert caught.value.request_ids == (f"req-{error_kind}",)
    assert caught.value.usage == LLMUsage(100, 25, 125, reasoning_tokens=5)


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


def test_r2b_evidence_type_exactly_matches_authoritative_contract():
    provider_enum = ProposedRequirement.model_json_schema()["properties"]["evidenceType"]["enum"]
    contract_enum = EvidenceRequirementV1.model_json_schema()["properties"]["evidenceType"]["enum"]
    assert provider_enum == contract_enum == [
        "fact", "comparison", "trend", "counter_evidence", "primary_source", "other",
    ]

    invalid = proposal()
    invalid["subquestions"][0]["requirements"][0]["evidenceType"] = (
        "Internal specifications and acceptance criteria"
    )
    with pytest.raises(ValueError):
        ProposedResearchPlan.model_validate(invalid)


def test_r2b_query_count_is_one_strict_four_item_collection():
    valid = proposal()
    valid["subquestions"][0]["queries"] = [
        {"source": "internal", "query": f"internal evidence {index}"}
        for index in range(4)
    ]
    assert len(ProposedResearchPlan.model_validate(valid).subquestions[0].queries) == 4

    invalid = proposal()
    invalid["subquestions"][0]["queries"] = [
        {"source": "internal", "query": f"internal evidence {index}"}
        for index in range(4)
    ] + [{"source": "web", "query": "external evidence"}]
    with pytest.raises(ValueError):
        ProposedResearchPlan.model_validate(invalid)

    schema = to_openai_schema(
        ProposedResearchPlan, preserve_strict_constraints=True
    )
    query_schema = schema["$defs"]["ProposedSubquestion"]["properties"]["queries"]
    assert query_schema["maxItems"] == 4


def test_r2b_assumption_confidence_exactly_matches_authoritative_contract():
    provider_enum = ProposedAssumption.model_json_schema()["properties"]["confidence"]["enum"]
    contract_enum = ResearchAssumptionV1.model_json_schema()["properties"]["confidence"]["enum"]
    assert provider_enum == contract_enum == ["low", "medium", "high"]
    with pytest.raises(ValueError):
        ProposedAssumption.model_validate({"description": "A", "confidence": "certain"})


def _keyword_paths(node, keyword, path="root"):
    paths = set()
    if isinstance(node, dict):
        if keyword in node:
            paths.add((path, str(node[keyword])))
        for key, value in node.items():
            paths.update(_keyword_paths(value, keyword, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            paths.update(_keyword_paths(value, keyword, f"{path}[{index}]"))
    return paths


@pytest.mark.parametrize("model", [ProposedResearchPlan, ProposedEvidenceAssessment])
def test_deep_provider_schema_preserves_all_supported_contract_constraints(model):
    raw = model.model_json_schema()
    strict = to_openai_schema(model, preserve_strict_constraints=True)
    for keyword in (
        "enum", "anyOf", "minLength", "maxLength", "minItems", "maxItems",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
        "pattern", "format",
    ):
        assert _keyword_paths(strict, keyword) == _keyword_paths(raw, keyword)

    def assert_strict_objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("required") == list(node.get("properties", {}))
                assert node.get("additionalProperties") is False
            for value in node.values():
                assert_strict_objects(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict_objects(value)

    assert_strict_objects(strict)


def test_r2c_response_dto_top_level_bounds_match_authoritative_contract():
    dto = ProposedEvidenceAssessment.model_json_schema()["properties"]
    contract = EvidenceAssessmentV1.model_json_schema()["properties"]
    for field in (
        "evidenceQuality", "requirementCoverage", "subquestionStatus",
        "conflicts", "prioritizedGaps", "explanation",
    ):
        for keyword in ("minItems", "maxItems", "minLength", "maxLength"):
            assert dto[field].get(keyword) == contract[field].get(keyword)
    assert dto["overallStatus"]["enum"] == contract["overallStatus"]["enum"]


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
    preflight = model.operation_preflight(prompt)
    assert preflight.max_calls == 2
    assert preflight.max_output_tokens_per_call == 512
    assert preflight.max_total_output_tokens == 1024
    assert preflight.max_cost_usd == model.max_operation_cost(prompt)


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


def test_r2b_and_r2c_have_distinct_conservative_default_completion_ceilings():
    environment = {
        "R2B_R2C_API_BASE_URL": "https://api.openai.com/v1",
        "R2B_R2C_API_KEY": "secret",
        "R2B_MODEL": "planner-model",
        "R2C_MODEL": "evaluation-model",
        "R2B_PRICING_VERIFIED": "true",
        "R2C_PRICING_VERIFIED": "true",
        "R2B_INPUT_COST_PER_MILLION_USD": "2",
        "R2B_OUTPUT_COST_PER_MILLION_USD": "10",
        "R2C_INPUT_COST_PER_MILLION_USD": "2",
        "R2C_OUTPUT_COST_PER_MILLION_USD": "10",
    }
    with patch.dict("os.environ", environment, clear=True):
        planning = OpenAICompatibleDeepPlanningModel.from_environment()
        evaluation = OpenAICompatibleEvidenceEvaluationModel.from_environment()

    assert planning.client.max_output_tokens == 2560
    assert evaluation.client.max_output_tokens == 3584
