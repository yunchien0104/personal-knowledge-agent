
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from personal_brain.research_agent import (
    ResearchAgentWorker,
    validate_plan,
)


QUESTION = "分析 A 公司營收"

PLAN = {
    "question": QUESTION,
    "scope": "分析 A 公司營收變化",
    "subquestions": ["A 公司營收是多少？"],
    "evidenceRequirements": ["需要 A 公司營收原始資料"],
    "maxCostUsd": 0,
    "maxQueries": 1,
    "maxRounds": 1,
    "stopConditions": ["取得足夠證據後停止"],
}

EVIDENCE = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "excerpt": "A 公司營收為 100 億元。",
    "locator": {},
    "assetId": "asset-1",
    "revisionId": "revision-1",
}


def make_worker():
    gateway = MagicMock()

    job = SimpleNamespace(
        id="test-job",
        lease_generation=1,
        payload=SimpleNamespace(
            job_type="execute_research",
            input={"question": QUESTION},
        ),
    )

    gateway.claim.return_value = job
    gateway.heartbeat.return_value = True
    gateway.research_context.return_value = {
        "evidence": [EVIDENCE]
    }

    planning_model = MagicMock()
    planning_model.plan.return_value = dict(PLAN)

    orchestrator = MagicMock()

    orchestrator.generator.provider = "test-provider"
    orchestrator.generator.model_name = "test-model"

    worker = ResearchAgentWorker(
        gateway,
        planning_model,
        "test-worker",
        orchestrator=orchestrator,
    )

    return worker, gateway, orchestrator


def test_plan_fixture():
    result = validate_plan(PLAN, QUESTION)
    assert result["question"] == QUESTION
    assert result["maxRounds"] == 1
    print("PASS: Planning fixture is valid")


def test_gate_blocks_publication():
    worker, gateway, orchestrator = make_worker()

    orchestrator.run.return_value = SimpleNamespace(
        gate=SimpleNamespace(can_publish=False)
    )

    result = worker.process_one()

    assert result.status == "failed", result.status
    orchestrator.run.assert_called_once()
    gateway.publish_research_draft.assert_not_called()
    gateway.report_failure.assert_called_once()

    kwargs = gateway.report_failure.call_args.kwargs

    assert kwargs["error_code"] == "research_verification_failed"
    assert kwargs["retryable"] is False

    print("PASS: Gate FAIL blocks publication")


def test_llm_failure_triggers_retry():
    worker, gateway, orchestrator = make_worker()

    orchestrator.run.side_effect = RuntimeError(
        "LLM connection failed"
    )

    result = worker.process_one()

    assert result.status == "retry_wait", result.status
    orchestrator.run.assert_called_once()
    gateway.publish_research_draft.assert_not_called()
    gateway.report_failure.assert_called_once()

    kwargs = gateway.report_failure.call_args.kwargs

    assert kwargs["error_code"] == "r2_llm_failure"
    assert kwargs["retryable"] is True

    print("PASS: LLM failure triggers retry")


def test_gate_pass_publishes():
    worker, gateway, orchestrator = make_worker()

    execution = SimpleNamespace(
        gate=SimpleNamespace(can_publish=True),
        verified_report="Verified report",
    )

    orchestrator.run.return_value = execution

    validated_draft = SimpleNamespace(
        title="A 公司營收分析",
        body="已驗證的研究內容",
        output_hash="a" * 64,
        findings=[
            {
                "text": "A 公司營收為 100 億元。",
                "uncertainty": "Source-supported fact.",
                "citations": [
                    {
                        "evidenceId": EVIDENCE["id"],
                        "quote": EVIDENCE["excerpt"],
                        "relationType": "supports",
                    }
                ],
            }
        ],
    )

    gateway.publish_research_draft.return_value = {
        "noteId": "test-note-id",
    }

    with patch(
        "personal_brain.research.publication_adapter.adapt_r2_publication",
        return_value=validated_draft,
    ) as adapter:

        result = worker.process_one()

        adapter.assert_called_once()

    assert result.status == "completed", result.status
    orchestrator.run.assert_called_once()
    gateway.publish_research_draft.assert_called_once()
    gateway.report_failure.assert_not_called()

    kwargs = gateway.publish_research_draft.call_args.kwargs

    assert kwargs["title"] == validated_draft.title
    assert kwargs["output_hash"] == validated_draft.output_hash
    assert kwargs["findings"] == validated_draft.findings

    print("PASS: Gate PASS publishes research")


if __name__ == "__main__":
    test_plan_fixture()
    test_gate_blocks_publication()
    test_llm_failure_triggers_retry()
    test_gate_pass_publishes()

    print("\nALL R2 WORKER TESTS PASSED")
