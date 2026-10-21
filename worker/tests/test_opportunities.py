from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import unittest
from unittest.mock import patch

from personal_brain.models import JobPayloadV1
from personal_brain.opportunities import (
    AutomaticBudgetExceeded,
    MINIMUM_SCORE,
    OpportunityError,
    OpportunityWorker,
    rank_opportunities,
)
from personal_brain.runner import research_suggestions_enabled
from personal_brain.supabase_gateway import ClaimedJob


USER_ID = "a1000000-0000-4000-8000-000000000001"
OTHER_USER_ID = "a2000000-0000-4000-8000-000000000002"
JOB_ID = "a3000000-0000-4000-8000-000000000003"
NOW = datetime(2026, 10, 21, 8, tzinfo=timezone.utc)


def signal(index: int, *, user_id: str = USER_ID, observed_at: datetime = NOW):
    return {
        "id": f"b{index:07d}-0000-4000-8000-000000000001",
        "userId": user_id,
        "signalType": "claim_gap",
        "text": f"Claim {index} requires independent confirmation.",
        "claimType": "source_assertion",
        "verificationStatus": "unreviewed",
        "needsReview": True,
        "evidenceIds": [f"c{index:07d}-0000-4000-8000-000000000001"],
        "observedAt": observed_at.isoformat().replace("+00:00", "Z"),
    }


def context(signals=None):
    return {
        "userId": USER_ID,
        "signals": signals if signals is not None else [signal(1)],
        "existingOpportunities": [],
        "completedQuestions": [],
        "automaticSpentUsd": "0",
        "estimatedRunCostUsd": "0",
    }


def claimed_job():
    payload = JobPayloadV1.from_dict({
        "schemaVersion": 1,
        "jobId": JOB_ID,
        "userId": USER_ID,
        "jobType": "rank_opportunities",
        "idempotencyKey": "opportunity-rank:2026-10-21",
        "input": {
            "rankingDate": "2026-10-21",
            "timezone": "Asia/Taipei",
            "minimumScore": 60,
            "maxNew": 10,
            "maxAutomaticCostUsd": "0.50",
        },
    })
    return ClaimedJob(JOB_ID, USER_ID, None, 1, payload)


class FakeGateway:
    def __init__(self, opportunity_context=None):
        self.job = claimed_job()
        self.claimed = False
        self.context = opportunity_context or context()
        self.publications = []
        self.failures = []
        self.paused = []
        self.research_starts = 0
        self.schedule_calls = 0

    def enqueue_due_opportunities(self):
        self.schedule_calls += 1
        return 1

    def claim(self, _worker_id, _lease_seconds, resource_class):
        self.resource_class = resource_class
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def heartbeat(self, _job, _worker_id, _lease_seconds):
        return True

    def opportunity_context(self, _job, _worker_id):
        return self.context

    def publish_opportunities(self, _job, _worker_id, **payload):
        self.publications.append(payload)
        return {"created": len(payload["candidates"]), "updated": 0}

    def pause_opportunity_budget(self, job, _worker_id):
        self.paused.append(job.id)

    def report_failure(self, _job, _worker_id, **failure):
        self.failures.append(failure)

    def start_research(self):
        self.research_starts += 1


class OpportunityRankingTests(unittest.TestCase):
    def test_opportunity_claim_accepts_null_asset_and_daily_input(self):
        job = claimed_job()
        parsed = ClaimedJob.from_row({
            "id": JOB_ID,
            "user_id": USER_ID,
            "asset_id": None,
            "lease_generation": 1,
            "payload_json": job.payload.to_dict(),
        })
        self.assertEqual(parsed.payload.job_type, "rank_opportunities")
        self.assertEqual(parsed.payload.input["rankingDate"], "2026-10-21")

    def test_feature_flag_defaults_off(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(research_suggestions_enabled())
        with patch.dict(os.environ, {"RESEARCH_SUGGESTIONS_ENABLED": "true"}, clear=True):
            self.assertTrue(research_suggestions_enabled())

    def test_rule_ranking_meets_threshold_and_is_deterministic(self):
        first = rank_opportunities(context(), now=NOW)
        second = rank_opportunities(context(), now=NOW)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertGreaterEqual(first[0].score, MINIMUM_SCORE)
        self.assertEqual(first[0].estimated_cost_usd, Decimal("0"))

    def test_candidate_below_threshold_is_not_recommended(self):
        weak = signal(1, observed_at=NOW - timedelta(days=365))
        weak.update({
            "claimType": "inference",
            "verificationStatus": "corroborated",
            "needsReview": False,
            "evidenceIds": [],
        })
        low_value = context([weak])
        low_value["completedQuestions"] = [
            "What independent evidence supports or contradicts this claim: "
            "Claim 1 requires independent confirmation."
        ]

        self.assertEqual(rank_opportunities(low_value, now=NOW), [])

    def test_daily_limit_allows_at_most_ten_new_suggestions(self):
        ranked = rank_opportunities(context([signal(index) for index in range(1, 16)]), now=NOW)
        self.assertEqual(len(ranked), 10)
        self.assertEqual(len({item.semantic_key for item in ranked}), 10)

    def test_duplicate_signals_are_idempotently_deduplicated(self):
        duplicate = signal(1)
        ranked = rank_opportunities(context([duplicate, dict(duplicate)]), now=NOW)
        self.assertEqual(len(ranked), 1)

    def test_feedback_cooldown_suppresses_candidate_until_expiry(self):
        baseline = rank_opportunities(context(), now=NOW)[0]
        during = context()
        during["existingOpportunities"] = [{
            "semanticKey": baseline.semantic_key,
            "cooldownUntil": (NOW + timedelta(days=7)).isoformat(),
            "status": "snoozed",
        }]
        self.assertEqual(rank_opportunities(during, now=NOW), [])

        after = dict(during)
        after["existingOpportunities"] = [{
            "semanticKey": baseline.semantic_key,
            "cooldownUntil": (NOW - timedelta(seconds=1)).isoformat(),
            "status": "snoozed",
        }]
        self.assertEqual(len(rank_opportunities(after, now=NOW)), 1)

    def test_shared_automatic_cost_cap_blocks_projected_overspend(self):
        over = context()
        over["automaticSpentUsd"] = "0.49"
        over["estimatedRunCostUsd"] = "0.02"
        with self.assertRaises(AutomaticBudgetExceeded):
            rank_opportunities(over, now=NOW)

        free = dict(over)
        free["estimatedRunCostUsd"] = "0"
        self.assertEqual(len(rank_opportunities(free, now=NOW)), 1)

    def test_foreign_tenant_signal_is_rejected(self):
        with self.assertRaisesRegex(OpportunityError, "tenant"):
            rank_opportunities(context([signal(1, user_id=OTHER_USER_ID)]), now=NOW)

    def test_worker_publishes_suggestions_without_starting_research(self):
        gateway = FakeGateway()
        worker = OpportunityWorker(gateway, "opportunity-worker")
        result = worker.process_one()
        idle = worker.process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(idle.status, "idle")
        self.assertEqual(gateway.resource_class, "opportunity")
        self.assertEqual(gateway.schedule_calls, 1)
        self.assertEqual(len(gateway.publications), 1)
        self.assertEqual(gateway.research_starts, 0)
        self.assertEqual(gateway.failures, [])

    def test_worker_pauses_instead_of_publishing_when_budget_is_exhausted(self):
        over = context()
        over["automaticSpentUsd"] = "0.50"
        over["estimatedRunCostUsd"] = "0.01"
        gateway = FakeGateway(over)

        result = OpportunityWorker(gateway, "opportunity-worker").process_one()

        self.assertEqual(result.status, "paused_budget")
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.paused, [JOB_ID])


if __name__ == "__main__":
    unittest.main()
