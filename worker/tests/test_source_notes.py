import json
import unittest

from personal_brain.models import JobPayloadV1
from personal_brain.source_notes import ModelError, NOTE_SCHEMA, SourceNoteWorker
from personal_brain.supabase_gateway import ClaimedJob, GatewayError


JOB_ID = "61000000-0000-4000-8000-000000000001"
USER_ID = "62000000-0000-4000-8000-000000000002"
ASSET_ID = "63000000-0000-4000-8000-000000000003"
REVISION_ID = "64000000-0000-4000-8000-000000000004"
EVIDENCE_ID = "65000000-0000-4000-8000-000000000005"
NOTE_ID = "66000000-0000-4000-8000-000000000006"
TOPIC_ID = "67000000-0000-4000-8000-000000000007"
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


def valid_output(*, legacy_quotes=False):
    fact_citation = {"evidenceId": EVIDENCE_ID}
    uncertainty_citation = {"evidenceId": EVIDENCE_ID}
    if legacy_quotes:
        fact_citation["quote"] = "Revenue increased by 20 percent in 2025."
        uncertainty_citation["quote"] = "seasonal uncertainty"
    return {
        "title": "2025 Revenue Review",
        "summary": "The source reports revenue growth with seasonal uncertainty.",
        "topicIds": [TOPIC_ID],
        "facts": [{"text": "Revenue increased in 2025.", "citations": [fact_citation]}],
        "dataPoints": [{"text": "The reported increase was 20 percent.", "citations": [fact_citation.copy()]}],
        "arguments": [],
        "conclusions": [],
        "uncertainties": [{
            "text": "The report identifies seasonal uncertainty.",
            "citations": [uncertainty_citation],
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
        self.topic_publications = []
        self.topic_options_error = None
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

    def topic_classification_options(self, _user_id):
        if self.topic_options_error:
            raise self.topic_options_error
        return [{"id": TOPIC_ID, "name": "金融與投資", "description": "金融市場與公司財務"}]

    def publish_source_note(self, _job, _worker_id, **publication):
        self.publications.append(publication)
        return {"noteId": NOTE_ID, "status": "completed"}

    def apply_ai_note_topics(self, note_id, topic_ids, status, error):
        self.topic_publications.append({
            "noteId": note_id, "topicIds": topic_ids, "status": status, "error": error,
        })
        return {"noteId": note_id, "topicIds": topic_ids, "status": status}

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
        self.assertTrue(all(
            citation["quote"] == EXCERPT
            for claim in publication["claims"] for citation in claim["citations"]
        ))
        self.assertEqual(gateway.failures, [])
        self.assertEqual(gateway.topic_publications[0]["topicIds"], [TOPIC_ID])
        self.assertEqual(gateway.topic_publications[0]["status"], "classified")

    def test_current_pdf_artifact_schema_is_supported(self):
        gateway = FakeGateway()
        gateway.artifact["schema_version"] = 2

        result = SourceNoteWorker(gateway, MockModel(), "note-worker").process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(gateway.publications), 1)

    def test_model_schema_requests_only_evidence_ids(self):
        citation_schema = NOTE_SCHEMA["properties"]["facts"]["items"]["properties"]["citations"]["items"]

        self.assertEqual(citation_schema["required"], ["evidenceId"])
        self.assertEqual(set(citation_schema["properties"]), {"evidenceId"})

    def test_invalid_topic_id_does_not_fail_the_note(self):
        output = valid_output()
        output["topicIds"] = ["68000000-0000-4000-8000-000000000008"]
        gateway = FakeGateway()

        result = SourceNoteWorker(gateway, MockModel(output), "note-worker").process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(gateway.publications), 1)
        self.assertEqual(gateway.topic_publications[0]["topicIds"], [])
        self.assertEqual(gateway.topic_publications[0]["status"], "failed")
        self.assertEqual(gateway.topic_publications[0]["error"], "invalid_topic_ids")

    def test_topic_option_failure_does_not_fail_the_note(self):
        gateway = FakeGateway()
        gateway.topic_options_error = GatewayError(
            "supabase_unavailable", "temporary", retryable=True,
        )

        result = SourceNoteWorker(gateway, MockModel(), "note-worker").process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(gateway.publications), 1)
        self.assertEqual(gateway.topic_publications[0]["status"], "failed")
        self.assertEqual(gateway.topic_publications[0]["error"], "topic_options_unavailable")

    def test_legacy_quote_format_remains_supported(self):
        gateway = FakeGateway()
        result = SourceNoteWorker(
            gateway, MockModel(valid_output(legacy_quotes=True)), "note-worker",
        ).process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            gateway.publications[0]["claims"][0]["citations"][0]["quote"],
            "Revenue increased by 20 percent in 2025.",
        )

    def test_threads_evidence_uses_the_same_grounded_source_note_pipeline(self):
        gateway = FakeGateway()
        source_url = "https://www.threads.net/t/DewcDm5n4FD"
        locator = {
            "kind": "threads_post", "url": source_url, "post_id": "DewcDm5n4FD",
            "start_offset": 0, "end_offset": len(EXCERPT), "source_hash": SOURCE_HASH,
        }
        gateway.context.update({"sourceType": "threads", "sourceUrl": source_url})
        gateway.context["evidence"][0]["locator"] = locator
        gateway.artifact = {
            "schema_version": 1, "source_type": "threads", "source_hash": SOURCE_HASH,
            "source_url": source_url,
            "segments": [{"text": EXCERPT, "evidence_locator": locator}],
        }

        result = SourceNoteWorker(gateway, MockModel(), "note-worker").process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(gateway.publications), 1)
        self.assertEqual(gateway.failures, [])

    def test_unknown_evidence_id_fails_without_publication(self):
        output = valid_output()
        output["facts"][0]["citations"][0]["evidenceId"] = "67000000-0000-4000-8000-000000000007"
        gateway = FakeGateway()
        result = SourceNoteWorker(gateway, MockModel(output), "note-worker").process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "invalid_evidence_id")
        self.assertFalse(gateway.failures[0]["retryable"])

    def test_non_verbatim_support_quote_fails_safely(self):
        output = valid_output(legacy_quotes=True)
        output["facts"][0]["citations"][0]["quote"] = "Revenue doubled."
        gateway = FakeGateway()
        result = SourceNoteWorker(gateway, MockModel(output), "note-worker").process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "quote_mismatch")

    def test_duplicate_evidence_id_fails_safely(self):
        output = valid_output()
        output["facts"][0]["citations"].append({"evidenceId": EVIDENCE_ID})
        gateway = FakeGateway()
        result = SourceNoteWorker(gateway, MockModel(output), "note-worker").process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "duplicate_evidence_id")

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
