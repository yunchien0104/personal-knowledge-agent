from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest

from personal_brain.models import JobPayloadV1
from personal_brain.runner import main

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


if __name__ == "__main__":
    unittest.main()
