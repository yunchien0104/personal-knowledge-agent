from __future__ import annotations

import os
from uuid import UUID

import pytest

from personal_brain.models import JobPayloadV1
from personal_brain.research.deep_coordinator import DeepResearchCoordinator
from personal_brain.retrieval_gateway import RetrievalSupabaseGateway
from personal_brain.supabase_gateway import ClaimedJob
from worker.tests.research.test_deep_coordinator import (
    FakeRetrieval,
    ProgressiveEvaluator,
    evidence_row,
)
from worker.tests.research.test_deep_finalization import (
    StaticPlanningProvider,
    draft,
    finalizer,
)


REQUIRED_ENV = (
    "FULL_DEEP_LOCAL_URL",
    "FULL_DEEP_LOCAL_SERVICE_KEY",
    "FULL_DEEP_LOCAL_JOB_ID",
    "FULL_DEEP_LOCAL_RUN_ID",
    "FULL_DEEP_LOCAL_WORKER_ID",
)


@pytest.mark.skipif(
    any(not os.getenv(name) for name in REQUIRED_ENV),
    reason="requires an explicitly seeded disposable Supabase Local stack",
)
def test_full_deep_pipeline_uses_real_local_persistence() -> None:
    user_id = UUID("fe000000-0000-4000-8000-000000000001")
    run_id = UUID(os.environ["FULL_DEEP_LOCAL_RUN_ID"])
    asset_one = UUID("fe000000-0000-4000-8000-000000000010")
    asset_two = UUID("fe000000-0000-4000-8000-000000000011")
    revision_one = UUID("fe000000-0000-4000-8000-000000000020")
    revision_two = UUID("fe000000-0000-4000-8000-000000000021")
    evidence_one = UUID("fe000000-0000-4000-8000-000000000030")
    evidence_two = UUID("fe000000-0000-4000-8000-000000000031")
    first = evidence_row(
        evidence_one, asset_one, revision_one, "first source establishes alpha"
    )
    second = evidence_row(
        evidence_two, asset_two, revision_two, "second source establishes beta"
    )
    payload = JobPayloadV1(
        os.environ["FULL_DEEP_LOCAL_JOB_ID"],
        str(user_id),
        "execute_research",
        "full-deep-local-e2e",
        {
            "researchRunId": str(run_id),
            "researchMode": "deep",
            "question": "Compare the two internal records",
        },
    )
    claimed = ClaimedJob(
        os.environ["FULL_DEEP_LOCAL_JOB_ID"],
        str(user_id),
        None,
        1,
        payload,
    )
    gateway = RetrievalSupabaseGateway(
        os.environ["FULL_DEEP_LOCAL_URL"],
        os.environ["FULL_DEEP_LOCAL_SERVICE_KEY"],
    )
    engine, generator_client, verifier_client = finalizer(
        draft(str(evidence_one), first["excerpt"])
    )

    result = DeepResearchCoordinator(
        gateway,
        FakeRetrieval({"first record": [first], "second record": [second]}),
        planner=StaticPlanningProvider(),
        evaluator=ProgressiveEvaluator(),
        finalizer=engine,
    ).process_claimed(claimed, os.environ["FULL_DEEP_LOCAL_WORKER_ID"], 120)

    assert result.status == "completed"
    assert generator_client.calls == 1
    assert verifier_client.calls == 1
