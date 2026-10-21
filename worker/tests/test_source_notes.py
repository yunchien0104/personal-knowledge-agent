import json
import unittest

from personal_brain.models import JobPayloadV1
from personal_brain.source_notes import ModelError, SourceNoteWorker
from personal_brain.supabase_gateway import ClaimedJob


JOB_ID = "61000000-0000-4000-8000-000000000001"
USER_ID = "62000000-0000-4000-8000-000000000002"
ASSET_ID = "63000000-0000-4000-8000-000000000003"
REVISION_ID = "64000000-0000-4000-8000-000000000004"
EVIDENCE_ID = "65000000-0000-4000-8000-000000000005"
NOTE_ID = "66000000-0000-4000-8000-000000000006"
SOURCE_HASH = "a" * 64
EXCERPT = "Revenue increased by 20 percent in 2025. The report notes seasonal uncertainty."


def claimed_job() -> ClaimedJob:
    payload = JobPayloadV1.from_dict({
        "schemaVersion": 1,
        "jobId": JOB_ID,
        "userId": USER_ID,
        "jobType": "generate_source_note",
        "idempotencyKey": f"source_note:{REVISION_ID}:prompt-v1",
        "input": {"assetId": ASSET_ID, "revisionId": REVISION_ID},
    })
    return ClaimedJob(JOB_ID, USER_ID, ASSET_ID, 1, payload)


def valid_output():
    citation = {"evidenceId": EVIDENCE_ID, "quote": "Revenue increased by 20 percent in 2025."}
    return {
        "title": "2025 Revenue Review",
        "summary": "The source reports revenue growth with seasonal uncertainty.",
        "topics": ["Revenue", "Seasonality"],
        "facts": [{"text": "Revenue increased in 2025.", "citations": [citation]}],
        "dataPoints": [{"text": "The reported increase was 20 percent.", "citations": [citation]}],
        "arguments": [],
        "conclusions": [],
        "uncertainties": [{
            "text": "The report identifies seasonal uncertainty.",
            "citations": [{"evidenceId": EVIDENCE_ID, "quote": "seasonal uncertainty"}],
        }],
    }


class MockModel:
    provider = "mock"
    model_name = "mock-structured-v1"

    def __init__(self, output=None, error=None):
        self.output = output or valid_output()
        self.error = error
        self.calls = 0

    def generate(self, _document):
        self.calls += 1
        if self.error:
            raise self.error
        return self.output


class FakeGateway:
    def __init__(self):
        self.job = claimed_job()
        self.claimed = False
        self.heartbeat_results = []
        self.publications = []
        self.failures = []
        self.context = {
            "assetId": ASSET_ID,
            "revisionId": REVISION_ID,
            "sourceFingerprint": SOURCE_HASH,
            "artifactObjectKey": f"{USER_ID}/{ASSET_ID}/revisions/{SOURCE_HASH}/native.json",
            "evidence": [{
                "id": EVIDENCE_ID,
                "excerpt": EXCERPT,
                "locator": {"kind": "pdf_page", "page_number": 1,
                            "start_offset": 0, "end_offset": len(EXCERPT),
                            "source_hash": SOURCE_HASH},
                "sourceHash": SOURCE_HASH,
            }],
        }
        self.artifact = {
            "schema_version": 1,
            "source_hash": SOURCE_HASH,
            "pages": [{"page_number": 1, "text": EXCERPT}],
        }

    def claim(self, _worker_id, _lease_seconds, resource_class):
        self.asserted_resource_class = resource_class
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def heartbeat(self, _job, _worker_id, _lease_seconds):
        return self.heartbeat_results.pop(0) if self.heartbeat_results else True

    def source_note_context(self, _job, _worker_id):
        return self.context

    def download_artifact(self, _path):
        return json.dumps(self.artifact).encode()

    def publish_source_note(self, _job, _worker_id, **publication):
        self.publications.append(publication)
        return {"noteId": NOTE_ID, "status": "completed"}

    def report_failure(self, _job, _worker_id, **failure):
        self.failures.append(failure)
        return "retry_wait" if failure["retryable"] else "failed"


class SourceNoteWorkerTests(unittest.TestCase):
    def test_mock_model_generates_grounded_editable_note(self):
        gateway = FakeGateway()
        model = MockModel()
        result = SourceNoteWorker(gateway, model, "note-worker").process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.note_id, NOTE_ID)
        self.assertEqual(gateway.asserted_resource_class, "llm")
        self.assertEqual(len(gateway.publications), 1)
        publication = gateway.publications[0]
        self.assertIn("## 摘要", publication["body"])
        self.assertEqual(len(publication["claims"]), 3)
        self.assertTrue(all(claim["citations"] for claim in publication["claims"]))
        self.assertEqual(gateway.failures, [])

    def test_unknown_evidence_id_fails_without_publication(self):
        output = valid_output()
        output["facts"][0]["citations"][0]["evidenceId"] = "67000000-0000-4000-8000-000000000007"
        gateway = FakeGateway()
        result = SourceNoteWorker(gateway, MockModel(output), "note-worker").process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "unsupported_claim")
        self.assertFalse(gateway.failures[0]["retryable"])

    def test_non_verbatim_support_quote_fails_safely(self):
        output = valid_output()
        output["facts"][0]["citations"][0]["quote"] = "Revenue doubled."
        gateway = FakeGateway()
        result = SourceNoteWorker(gateway, MockModel(output), "note-worker").process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "unsupported_claim")

    def test_transient_model_failure_requests_retry(self):
        gateway = FakeGateway()
        model = MockModel(error=ModelError("llm_unavailable", "temporary", retryable=True))
        result = SourceNoteWorker(gateway, model, "note-worker").process_one()

        self.assertEqual(result.status, "retry_wait")
        self.assertEqual(gateway.publications, [])
        self.assertTrue(gateway.failures[0]["retryable"])

    def test_stale_lease_never_calls_model_or_publishes(self):
        gateway = FakeGateway()
        gateway.heartbeat_results = [False]
        model = MockModel()
        result = SourceNoteWorker(gateway, model, "stale-worker").process_one()

        self.assertEqual(result.status, "lease_lost")
        self.assertEqual(model.calls, 0)
        self.assertEqual(gateway.publications, [])


if __name__ == "__main__":
    unittest.main()
