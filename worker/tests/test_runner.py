from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from personal_brain.models import JobPayloadV1
from personal_brain.runner import build_queue_worker, main

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
            "deletion", "pdf", "threads", "instagram_post", "instagram_reel",
            "source_note", "retrieval", "research",
        )}
        builders = {
            "build_deletion_worker": workers["deletion"],
            "build_ingestion_worker": workers["pdf"],
            "build_threads_worker": workers["threads"],
            "build_instagram_post_worker": workers["instagram_post"],
            "build_instagram_reel_worker": workers["instagram_reel"],
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

        self.assertIn(workers["threads"], dispatcher.workers)


if __name__ == "__main__":
    unittest.main()
