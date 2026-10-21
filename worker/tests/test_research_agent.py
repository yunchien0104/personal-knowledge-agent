import unittest

from personal_brain.models import JobPayloadV1
from personal_brain.research_agent import ResearchAgentWorker
from personal_brain.supabase_gateway import ClaimedJob


JOB_ID = "91000000-0000-4000-8000-000000000001"
USER_ID = "92000000-0000-4000-8000-000000000002"
RUN_ID = "93000000-0000-4000-8000-000000000003"
ASSET_ID = "94000000-0000-4000-8000-000000000004"
REVISION_ID = "95000000-0000-4000-8000-000000000005"
EVIDENCE_ID = "96000000-0000-4000-8000-000000000006"
NOTE_ID = "97000000-0000-4000-8000-000000000007"
QUESTION = "What does the evidence say about revenue growth?"
EXCERPT = "Revenue increased by 20 percent in 2025, with seasonal uncertainty."


def claimed_job() -> ClaimedJob:
    payload = JobPayloadV1.from_dict({
        "schemaVersion": 1,
        "jobId": JOB_ID,
        "userId": USER_ID,
        "jobType": "execute_research",
        "idempotencyKey": "research:98000000-0000-4000-8000-000000000008",
        "input": {"researchRunId": RUN_ID, "question": QUESTION},
    })
    return ClaimedJob(JOB_ID, USER_ID, None, 1, payload)


def valid_plan():
    return {
        "question": QUESTION,
        "scope": "Internal evidence only",
        "subquestions": ["revenue growth"],
        "evidenceRequirements": ["Original evidence with verbatim quotes"],
        "maxCostUsd": 0,
        "maxQueries": 1,
        "maxRounds": 1,
        "stopConditions": ["One internal retrieval round"],
    }


def valid_synthesis():
    return {
        "title": "Revenue Growth Research Draft",
        "summary": "The internal evidence reports growth and uncertainty.",
        "findings": [{
            "text": "The source reports 20 percent revenue growth in 2025.",
            "uncertainty": "This is one source and is not independently verified.",
            "citations": [{
                "evidenceId": EVIDENCE_ID,
                "quote": "Revenue increased by 20 percent in 2025",
                "relationType": "supports",
            }],
        }],
    }


class MockResearchModel:
    provider = "mock"
    model_name = "mock-research-v1"

    def __init__(self, *, plan=None, synthesis=None):
        self.plan_output = plan or valid_plan()
        self.synthesis_output = synthesis or valid_synthesis()
        self.plan_calls = 0
        self.synthesis_calls = 0

    def plan(self, _question):
        self.plan_calls += 1
        return self.plan_output

    def synthesize(self, _question, _plan, _evidence):
        self.synthesis_calls += 1
        return self.synthesis_output


class FakeGateway:
    def __init__(self):
        self.job = claimed_job()
        self.claimed = False
        self.heartbeat_results = []
        self.failures = []
        self.publications = []
        self.context = {
            "researchRunId": RUN_ID,
            "question": QUESTION,
            "plan": valid_plan(),
            "evidence": [{
                "id": EVIDENCE_ID,
                "assetId": ASSET_ID,
                "revisionId": REVISION_ID,
                "excerpt": EXCERPT,
                "locator": {"kind": "pdf_page", "page_number": 2},
                "relevance": 0.75,
            }],
        }

    def claim(self, _worker_id, _lease_seconds, resource_class):
        self.resource_class = resource_class
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def heartbeat(self, _job, _worker_id, _lease_seconds):
        return self.heartbeat_results.pop(0) if self.heartbeat_results else True

    def research_context(self, _job, _worker_id, plan):
        self.plan = plan
        return self.context

    def publish_research_draft(self, _job, _worker_id, **publication):
        self.publications.append(publication)
        return {"noteId": NOTE_ID, "researchRunId": RUN_ID, "status": "draft_ready"}

    def report_failure(self, _job, _worker_id, **failure):
        self.failures.append(failure)
        return "retry_wait" if failure["retryable"] else "failed"


class ResearchAgentWorkerTests(unittest.TestCase):
    def test_research_claim_accepts_null_asset_and_typed_run_input(self):
        job = claimed_job()
        parsed = ClaimedJob.from_row({
            "id": JOB_ID,
            "user_id": USER_ID,
            "asset_id": None,
            "lease_generation": 1,
            "payload_json": job.payload.to_dict(),
        })
        self.assertIsNone(parsed.asset_id)
        self.assertEqual(parsed.payload.input["researchRunId"], RUN_ID)

    def test_plan_retrieve_select_synthesize_and_publish_grounded_draft(self):
        gateway = FakeGateway()
        result = ResearchAgentWorker(gateway, MockResearchModel(), "research-worker").process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.note_id, NOTE_ID)
        self.assertEqual(gateway.resource_class, "research")
        self.assertEqual(gateway.plan["maxCostUsd"], 0)
        self.assertEqual(len(gateway.publications), 1)
        publication = gateway.publications[0]
        self.assertIn(f"[Evidence {EVIDENCE_ID}]", publication["body"])
        self.assertEqual(publication["findings"][0]["citations"][0]["quote"],
                         "Revenue increased by 20 percent in 2025")
        self.assertEqual(gateway.failures, [])

    def test_unknown_or_non_verbatim_citation_never_publishes(self):
        synthesis = valid_synthesis()
        synthesis["findings"][0]["citations"][0]["quote"] = "Revenue doubled."
        gateway = FakeGateway()
        result = ResearchAgentWorker(
            gateway, MockResearchModel(synthesis=synthesis), "research-worker"
        ).process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "unsupported_research_finding")

    def test_empty_internal_retrieval_is_truthful_insufficient_evidence(self):
        gateway = FakeGateway()
        gateway.context["evidence"] = []
        model = MockResearchModel()
        result = ResearchAgentWorker(gateway, model, "research-worker").process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(model.synthesis_calls, 0)
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "insufficient_evidence")

    def test_stale_lease_stops_before_planning(self):
        gateway = FakeGateway()
        gateway.heartbeat_results = [False]
        model = MockResearchModel()
        result = ResearchAgentWorker(gateway, model, "research-worker").process_one()

        self.assertEqual(result.status, "lease_lost")
        self.assertEqual(model.plan_calls, 0)
        self.assertEqual(gateway.publications, [])


if __name__ == "__main__":
    unittest.main()
