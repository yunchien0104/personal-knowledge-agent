import json
import os
import unittest
from unittest.mock import patch

from personal_brain.models import JobPayloadV1
from personal_brain.source_notes import LocalExtractiveStructuredNoteModel, SourceNoteWorker
from personal_brain.supabase_gateway import ClaimedJob
from personal_brain.threads_ingestion import (
    ThreadsFetchError,
    ThreadsFetchResult,
    ThreadsIngestionWorker,
    classify_playwright_error,
    configure_playwright_browser_path,
    normalize_threads_url,
    parse_threads_post,
)


JOB_ID = "71000000-0000-4000-8000-000000000001"
USER_ID = "72000000-0000-4000-8000-000000000002"
ASSET_ID = "73000000-0000-4000-8000-000000000003"
REVISION_ID = "74000000-0000-4000-8000-000000000004"
EVIDENCE_ID = "75000000-0000-4000-8000-000000000005"
NOTE_JOB_ID = "76000000-0000-4000-8000-000000000006"
NOTE_ID = "77000000-0000-4000-8000-000000000007"
POST_ID = "DewcDm5n4FD"
SOURCE_URL = f"https://www.threads.net/t/{POST_ID}"


def claimed_job(provided_text: str | None = None) -> ClaimedJob:
    job_input = {
        "assetId": ASSET_ID,
        "sourceUrl": SOURCE_URL,
        "sourceExternalId": POST_ID,
    }
    if provided_text is not None:
        job_input["providedText"] = provided_text
    payload = JobPayloadV1.from_dict({
        "schemaVersion": 1,
        "jobId": JOB_ID,
        "userId": USER_ID,
        "jobType": "ingest_threads",
        "idempotencyKey": f"ingest_threads:{ASSET_ID}:source-v1",
        "input": job_input,
    })
    return ClaimedJob(JOB_ID, USER_ID, ASSET_ID, 1, payload, "2026-10-22T00:00:00Z")


class FakeGateway:
    def __init__(self) -> None:
        self.job = claimed_job()
        self.claimed = False
        self.uploads = []
        self.publications = []
        self.failures = []

    def claim(self, _worker_id, _lease_seconds, resource_class="lightweight"):
        self.resource_class = resource_class
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def heartbeat(self, _job, _worker_id, _lease_seconds):
        return True

    def upload_immutable(self, path, content, content_hash):
        self.uploads.append((path, content, content_hash))

    def publish_threads(self, _job, _worker_id, **publication):
        self.publications.append(publication)
        return {"revisionId": REVISION_ID, "status": "completed"}

    def report_failure(self, _job, _worker_id, **failure):
        self.failures.append(failure)
        return "retry_wait" if failure["retryable"] else "failed"


class SourceNoteGateway:
    def __init__(self, ingestion_gateway):
        payload = JobPayloadV1.from_dict({
            "schemaVersion": 1,
            "jobId": NOTE_JOB_ID,
            "userId": USER_ID,
            "jobType": "generate_source_note",
            "idempotencyKey": f"source_note:{REVISION_ID}:prompt-v1",
            "input": {"assetId": ASSET_ID, "revisionId": REVISION_ID},
        })
        self.job = ClaimedJob(NOTE_JOB_ID, USER_ID, ASSET_ID, 1, payload)
        self.claimed = False
        self.publications = []
        self.failures = []
        object_path, artifact, _artifact_hash = ingestion_gateway.uploads[0]
        publication = ingestion_gateway.publications[0]
        segment = publication["segments"][0]
        self.artifact = artifact
        self.context = {
            "assetId": ASSET_ID,
            "revisionId": REVISION_ID,
            "sourceType": "threads",
            "sourceUrl": segment["evidence_locator"]["url"],
            "sourceFingerprint": publication["source_hash"],
            "artifactObjectKey": object_path,
            "evidence": [{
                "id": EVIDENCE_ID,
                "excerpt": segment["text"],
                "locator": segment["evidence_locator"],
                "sourceHash": publication["source_hash"],
            }],
        }

    def claim(self, _worker_id, _lease_seconds, resource_class="lightweight"):
        self.resource_class = resource_class
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def heartbeat(self, _job, _worker_id, _lease_seconds):
        return True

    def source_note_context(self, _job, _worker_id):
        return self.context

    def download_artifact(self, object_path):
        if object_path != self.context["artifactObjectKey"]:
            raise AssertionError("unexpected artifact path")
        return self.artifact

    def topic_classification_options(self, _user_id):
        return []

    def publish_source_note(self, _job, _worker_id, **publication):
        self.publications.append(publication)
        return {"noteId": NOTE_ID, "status": "completed"}

    def apply_ai_note_topics(self, _note_id, _topic_ids, _status, _error):
        return {"status": "ok"}

    def report_failure(self, _job, _worker_id, **failure):
        self.failures.append(failure)
        return "retry_wait" if failure["retryable"] else "failed"


class ThreadsIngestionTests(unittest.TestCase):
    def test_public_fetch_uses_hermetic_browser_location(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
            self.assertEqual(configure_playwright_browser_path(), "0")
            self.assertEqual(os.environ["PLAYWRIGHT_BROWSERS_PATH"], "0")

    def test_public_fetch_preserves_explicit_browser_location(self):
        with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "/custom"}):
            self.assertEqual(configure_playwright_browser_path(), "/custom")

    def test_missing_browser_runtime_is_permanent_and_actionable(self):
        error = classify_playwright_error(
            RuntimeError("BrowserType.launch: Executable doesn't exist; playwright install")
        )

        self.assertEqual(error.code, "browser_runtime_missing")
        self.assertFalse(error.retryable)

    def test_remote_browser_failure_remains_retryable(self):
        error = classify_playwright_error(RuntimeError("net::ERR_CONNECTION_RESET"))

        self.assertEqual(error.code, "threads_fetch_failed")
        self.assertTrue(error.retryable)

    def test_url_normalization_strips_tracking_and_deduplicates_by_post_id(self):
        target = normalize_threads_url(
            f"https://www.threads.com/@author/post/{POST_ID}?xmt=tracking#fragment"
        )
        self.assertEqual(
            target.canonical_url,
            f"https://www.threads.net/@author/post/{POST_ID}",
        )
        self.assertEqual(target.post_id, POST_ID)
        with self.assertRaises(ThreadsFetchError):
            normalize_threads_url("https://example.com/@author/post/DewcDm5n4FD")

    def test_chunking_preserves_post_locator_offsets_and_overlap(self):
        target = normalize_threads_url(SOURCE_URL)
        parsed = parse_threads_post(
            target,
            ThreadsFetchResult("x" * 210, "open_graph"),
            retrieved_at="2026-10-22T00:00:00Z",
        )
        self.assertEqual([(s.evidence_locator["start_offset"], s.evidence_locator["end_offset"])
                          for s in parsed.segments], [(0, 150), (130, 210)])
        self.assertTrue(all(s.evidence_locator["post_id"] == POST_ID for s in parsed.segments))
        self.assertTrue(all(s.evidence_locator["url"] == SOURCE_URL for s in parsed.segments))

    def test_accessible_post_publishes_one_immutable_revision(self):
        gateway = FakeGateway()
        worker = ThreadsIngestionWorker(
            gateway,
            "threads-worker",
            fetcher=lambda _target: ThreadsFetchResult("Public post evidence.", "open_graph"),
        )
        result = worker.process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.revision_id, REVISION_ID)
        self.assertEqual(gateway.resource_class, "media")
        self.assertEqual(len(gateway.uploads), 1)
        artifact = json.loads(gateway.uploads[0][1])
        self.assertEqual(artifact["source_url"], SOURCE_URL)
        self.assertEqual(artifact["segments"][0]["text"], "Public post evidence.")
        self.assertEqual(len(gateway.publications), 1)
        self.assertEqual(gateway.failures, [])

    def test_user_provided_text_bypasses_public_fetch_and_remains_citable(self):
        gateway = FakeGateway()
        gateway.job = claimed_job("  User supplied Threads evidence.  ")

        def unexpected_fetch(_target):
            self.fail("public fetch must not run when fallback text was provided")

        result = ThreadsIngestionWorker(
            gateway, "threads-worker", fetcher=unexpected_fetch
        ).process_one()

        self.assertEqual(result.status, "completed")
        artifact = json.loads(gateway.uploads[0][1])
        self.assertEqual(artifact["text"], "User supplied Threads evidence.")
        self.assertEqual(artifact["provenance"]["retrieval_method"], "user_provided")

    def test_single_url_flows_through_evidence_to_source_note_citation(self):
        original_text = "Public Threads evidence remains tied to its original citation URL."
        ingestion_gateway = FakeGateway()
        ingestion = ThreadsIngestionWorker(
            ingestion_gateway,
            "threads-worker",
            fetcher=lambda _target: ThreadsFetchResult(original_text, "open_graph"),
        ).process_one()
        self.assertEqual(ingestion.status, "completed")

        note_gateway = SourceNoteGateway(ingestion_gateway)
        note = SourceNoteWorker(
            note_gateway, LocalExtractiveStructuredNoteModel(), "source-note-worker"
        ).process_one()

        self.assertEqual(note.status, "completed")
        self.assertEqual(note.note_id, NOTE_ID)
        self.assertEqual(note_gateway.resource_class, "llm")
        citation = note_gateway.publications[0]["claims"][0]["citations"][0]
        self.assertEqual(citation, {"evidenceId": EVIDENCE_ID, "quote": original_text})
        self.assertEqual(note_gateway.context["evidence"][0]["locator"]["url"], SOURCE_URL)
        self.assertEqual(note_gateway.failures, [])

    def test_inaccessible_or_incomplete_post_never_publishes_partial_evidence(self):
        for code in ("threads_post_inaccessible", "threads_content_incomplete"):
            with self.subTest(code=code):
                gateway = FakeGateway()

                def fail(_target, error_code=code):
                    raise ThreadsFetchError(error_code, "manual fallback required")

                result = ThreadsIngestionWorker(
                    gateway, "threads-worker", fetcher=fail
                ).process_one()
                self.assertEqual(result.status, "failed")
                self.assertEqual(gateway.uploads, [])
                self.assertEqual(gateway.publications, [])
                self.assertEqual(gateway.failures[0]["error_class"], "unsupported")
                self.assertEqual(gateway.failures[0]["error_code"], code)


if __name__ == "__main__":
    unittest.main()
