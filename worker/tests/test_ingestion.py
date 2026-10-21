from pathlib import Path
import tempfile
import unittest

from personal_brain.ingestion import PdfIngestionWorker
from personal_brain.models import JobPayloadV1
from personal_brain.supabase_gateway import ClaimedJob, GatewayError


FIXTURES = Path(__file__).parent / "fixtures"


def claimed_job() -> ClaimedJob:
    job_id = "51000000-0000-4000-8000-000000000001"
    user_id = "52000000-0000-4000-8000-000000000002"
    asset_id = "53000000-0000-4000-8000-000000000003"
    payload = JobPayloadV1.from_dict({
        "schemaVersion": 1,
        "jobId": job_id,
        "userId": user_id,
        "jobType": "ingest_pdf",
        "idempotencyKey": "worker-ingestion-test",
        "input": {
            "assetId": asset_id,
            "storageBucket": "asset-private",
            "objectPath": f"{user_id}/{asset_id}/original/source.pdf",
        },
    })
    return ClaimedJob(job_id, user_id, asset_id, 1, payload)


class FakeGateway:
    def __init__(self, source: bytes) -> None:
        self.job = claimed_job()
        self.source = source
        self.claimed = False
        self.heartbeat_results: list[bool] = []
        self.heartbeat_count = 0
        self.uploads: list[tuple[str, bytes, str]] = []
        self.publications: list[dict[str, object]] = []
        self.failures: list[dict[str, object]] = []
        self.download_error: GatewayError | None = None

    def claim(self, _worker_id: str, _lease_seconds: int) -> ClaimedJob | None:
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def heartbeat(self, _job: ClaimedJob, _worker_id: str, _lease_seconds: int) -> bool:
        self.heartbeat_count += 1
        return self.heartbeat_results.pop(0) if self.heartbeat_results else True

    def download_original(self, _job: ClaimedJob) -> bytes:
        if self.download_error:
            raise self.download_error
        return self.source

    def upload_immutable(self, path: str, content: bytes, content_hash: str) -> None:
        self.uploads.append((path, content, content_hash))

    def publish(self, _job: ClaimedJob, _worker_id: str, **result: object):
        self.publications.append(result)
        return {"status": "completed", "revisionId": "54000000-0000-4000-8000-000000000004"}

    def report_failure(self, _job: ClaimedJob, _worker_id: str, **failure: object):
        self.failures.append(failure)
        return "retry_wait" if failure["retryable"] else "failed"


class PdfIngestionWorkerTests(unittest.TestCase):
    def test_successful_ingestion_uploads_and_publishes_then_cleans_temp_files(self) -> None:
        gateway = FakeGateway((FIXTURES / "native-text.pdf").read_bytes())
        with tempfile.TemporaryDirectory() as root:
            result = PdfIngestionWorker(
                gateway, "test-worker", temporary_root=Path(root)
            ).process_one()
            self.assertEqual(list(Path(root).iterdir()), [])

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.revision_id, "54000000-0000-4000-8000-000000000004")
        self.assertGreaterEqual(gateway.heartbeat_count, 4)
        self.assertEqual(len(gateway.uploads), 1)
        self.assertEqual(len(gateway.publications), 1)
        pages = gateway.publications[0]["pages"]
        self.assertEqual([page["page_number"] for page in pages], [1, 2])
        self.assertEqual(gateway.failures, [])

    def test_lost_lease_never_downloads_or_publishes(self) -> None:
        gateway = FakeGateway((FIXTURES / "native-text.pdf").read_bytes())
        gateway.heartbeat_results = [False]
        result = PdfIngestionWorker(gateway, "stale-worker").process_one()

        self.assertEqual(result.status, "lease_lost")
        self.assertEqual(gateway.uploads, [])
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures, [])

    def test_scanned_pdf_reports_safe_unsupported_failure(self) -> None:
        gateway = FakeGateway((FIXTURES / "scanned-image-only.pdf").read_bytes())
        result = PdfIngestionWorker(gateway, "test-worker").process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_class"], "unsupported")
        self.assertEqual(gateway.failures[0]["error_code"], "scanned_pdf")
        self.assertFalse(gateway.failures[0]["retryable"])

    def test_transient_storage_failure_requests_retry_without_publication(self) -> None:
        gateway = FakeGateway((FIXTURES / "native-text.pdf").read_bytes())
        gateway.download_error = GatewayError(
            "storage_unavailable", "Storage is temporarily unavailable", retryable=True
        )
        result = PdfIngestionWorker(gateway, "test-worker").process_one()

        self.assertEqual(result.status, "retry_wait")
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_class"], "transient")
        self.assertTrue(gateway.failures[0]["retryable"])

    def test_idle_queue_does_no_work(self) -> None:
        gateway = FakeGateway((FIXTURES / "native-text.pdf").read_bytes())
        gateway.claimed = True
        result = PdfIngestionWorker(gateway, "test-worker").process_one()
        self.assertEqual(result.status, "idle")
        self.assertEqual(gateway.heartbeat_count, 0)


if __name__ == "__main__":
    unittest.main()
