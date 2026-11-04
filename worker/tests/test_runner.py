from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from personal_brain.models import JobPayloadV1
from personal_brain.queue_runtime import ProcessResult
from personal_brain.runner import MediaJobDispatcher, build_queue_worker, main
from personal_brain.supabase_gateway import ClaimedJob

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPOSITORY_ROOT / "packages/contracts/fixtures/job-payload.v1.json"


class RunnerTests(unittest.TestCase):
    def test_fixture_matches_python_contract(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(JobPayloadV1.from_dict(fixture).to_dict(), fixture)

    def test_runner_executes_registered_handler(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["--payload", str(FIXTURE)])

        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["result"]["workerReady"])

    def test_all_capability_registers_threads_worker(self) -> None:
        workers = {name: object() for name in (
            "deletion", "pdf", "media", "source_note", "retrieval", "research",
        )}
        builders = {
            "build_deletion_worker": workers["deletion"],
            "build_ingestion_worker": workers["pdf"],
            "build_media_dispatcher": workers["media"],
            "build_source_note_worker": workers["source_note"],
            "build_retrieval_index_worker": workers["retrieval"],
            "build_research_worker": workers["research"],
        }
        patchers = [
            patch(f"personal_brain.runner.{name}", return_value=worker)
            for name, worker in builders.items()
        ]
        with patch.dict(os.environ, {"WORKER_CAPABILITY": "all"}), \
             patch("personal_brain.runner.research_suggestions_enabled", return_value=False):
            for builder_patch in patchers:
                builder_patch.start()
            try:
                dispatcher = build_queue_worker()
            finally:
                for builder_patch in reversed(patchers):
                    builder_patch.stop()

        self.assertIn(workers["media"], dispatcher.workers)

    def test_media_dispatcher_routes_reel_without_other_handlers_claiming_it(self) -> None:
        payload = JobPayloadV1.from_dict({
            "schemaVersion": 1,
            "jobId": "11111111-1111-4111-8111-111111111111",
            "userId": "22222222-2222-4222-8222-222222222222",
            "jobType": "ingest_instagram_reel",
            "idempotencyKey": "reel:test",
            "input": {"assetId": "33333333-3333-4333-8333-333333333333"},
        })
        job = ClaimedJob(
            payload.job_id,
            payload.user_id,
            payload.input["assetId"],
            1,
            payload,
        )

        class Gateway:
            def __init__(self):
                self.claims = 0

            def claim(self, *_args):
                self.claims += 1
                return job if self.claims == 1 else None

        class Worker:
            def __init__(self, gateway, status):
                self.gateway = gateway
                self.worker_id = "worker-test"
                self.lease_seconds = 120
                self.status = status
                self.called = False

            def process_one(self):
                claimed = self.gateway.claim(self.worker_id, self.lease_seconds, "media")
                self.called = claimed is job
                return ProcessResult(self.status, claimed.id)

        gateway = Gateway()
        threads = Worker(gateway, "threads")
        reel = Worker(gateway, "completed")
        dispatcher = MediaJobDispatcher({
            "ingest_threads": threads,
            "ingest_instagram_reel": reel,
        })

        result = dispatcher.process_one()

        self.assertEqual(result.status, "completed")
        self.assertTrue(reel.called)
        self.assertFalse(threads.called)
        self.assertEqual(gateway.claims, 1)


if __name__ == "__main__":
    unittest.main()
