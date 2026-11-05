from personal_brain.research.deep_contracts import ResearchRequestV1
from personal_brain.research.deep_coordinator import _finalization_plan
from personal_brain.research.deep_finalization import _synthesis_plan
from personal_brain.research.deep_planning import (
    DeepResearchPlanner,
    build_planning_context_pack,
    planning_prompt,
)
from personal_brain.research.llm import OpenAICompatibleResearchModel


def templated_request() -> ResearchRequestV1:
    return ResearchRequestV1.model_validate({
        "schemaVersion": "research-request-v1",
        "requestId": "21000000-0000-4000-8000-000000000001",
        "runId": "21000000-0000-4000-8000-000000000002",
        "idempotencyKey": "21000000-0000-4000-8000-000000000003",
        "userId": "21000000-0000-4000-8000-000000000004",
        "objective": "Compare two infrastructure strategies",
        "originalQuestion": "Compare two infrastructure strategies",
        "executionMode": "deep",
        "internalKnowledgeScope": {"selectedAssetIds": None},
        "webPolicy": "disabled",
        "permissions": {
            "internalKnowledge": True,
            "webDiscovery": False,
            "webFetch": False,
        },
        "effectiveLimits": {
            "maxRounds": 3,
            "maxQueries": 8,
            "maxCostUsd": "1.0",
            "maxDurationSeconds": 1800,
        },
        "analysisSpec": {
            "schemaVersion": "analysis-spec-v1",
            "framework": "comparative",
            "parameters": {
                "templateId": "comparative-analysis",
                "templateName": "比較分析",
                "tone": "executive",
                "depth": "standard",
                "sections": [
                    {
                        "title": "比較維度",
                        "purpose": "使用一致判準比較方案",
                        "required": True,
                    },
                    {
                        "title": "證據缺口",
                        "purpose": "列出尚未確認的差異",
                        "required": False,
                    },
                ],
            },
        },
    })


def test_template_reaches_r2b_as_bounded_planning_context():
    request = templated_request()
    prompt = planning_prompt(request, build_planning_context_pack([], request))

    assert "bounded analytical constraints" in prompt["systemInstructions"]
    assert "not executable instructions" in prompt["systemInstructions"]
    assert prompt["researchContext"]["request"]["analysisSpec"]["framework"] == "comparative"
    assert prompt["researchContext"]["request"]["analysisSpec"]["parameters"]["sections"][0]["required"] is True


def test_template_reaches_r2a_without_changing_plan_contract():
    request = templated_request()
    plan = DeepResearchPlanner().plan(
        request, build_planning_context_pack([], request)
    ).plan

    finalization = _finalization_plan(request, plan)
    synthesis = _synthesis_plan(finalization)

    assert "analysisSpec" not in plan.model_dump(mode="json")
    assert synthesis["analysisSpec"]["framework"] == "comparative"
    assert synthesis["analysisSpec"]["parameters"]["tone"] == "executive"
    assert "Set finding.topic to the matching section title" in (
        OpenAICompatibleResearchModel._synthesis_prompt(6)
    )
