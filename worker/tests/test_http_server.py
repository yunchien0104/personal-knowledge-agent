import json
from http import HTTPStatus
from threading import Event, Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from personal_brain.http_server import create_server, poll_queue
from personal_brain.ingestion import ProcessResult


SECRET = "test-run-once-secret-with-32-characters"


class FakeWorker:
    def __init__(self, result: ProcessResult | None = None) -> None:
        self.calls = 0
        self.result = result or ProcessResult("idle")

    def process_one(self) -> ProcessResult:
        self.calls += 1
        return self.result


class BlockingWorker(FakeWorker):
    def __init__(self) -> None:
        super().__init__(ProcessResult("completed", "job-1"))
        self.started = Event()
        self.release = Event()

    def process_one(self) -> ProcessResult:
        self.calls += 1
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test worker did not release")
        return self.result


class SequenceWorker(FakeWorker):
    def __init__(self) -> None:
        super().__init__()
        self.idle_reached = Event()

    def process_one(self) -> ProcessResult:
        self.calls += 1
        if self.calls == 1:
            return ProcessResult("completed", "job-1")
        self.idle_reached.set()
        return ProcessResult("idle")


class WorkerHttpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.worker = FakeWorker(ProcessResult("completed", "job-1", "revision-1"))
        self.server = create_server(self.worker, SECRET, host="127.0.0.1", port=0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path: str, *, method: str = "GET", secret: str | None = None):
        headers = {"Authorization": f"Bearer {secret}"} if secret is not None else {}
        request = Request(f"{self.base_url}{path}", method=method, headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_health_is_public_and_side_effect_free(self) -> None:
        status, body = self.request("/health")

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body, {"status": "ok"})
        self.assertEqual(self.worker.calls, 0)

    def test_run_once_rejects_missing_and_incorrect_secret(self) -> None:
        missing = self.request("/run-once", method="POST")
        incorrect = self.request("/run-once", method="POST", secret="wrong")

        self.assertEqual(missing[0], HTTPStatus.UNAUTHORIZED)
        self.assertEqual(incorrect[0], HTTPStatus.UNAUTHORIZED)
        self.assertEqual(self.worker.calls, 0)

    def test_run_once_processes_exactly_one_job_attempt(self) -> None:
        status, body = self.request("/run-once", method="POST", secret=SECRET)

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["job_id"], "job-1")
        self.assertEqual(self.worker.calls, 1)

    def test_run_once_rejects_parallel_request(self) -> None:
        self.tearDown()
        worker = BlockingWorker()
        self.worker = worker
        self.server = create_server(worker, SECRET, host="127.0.0.1", port=0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

        first_result: list[tuple[int, dict[str, str]]] = []
        first = Thread(
            target=lambda: first_result.append(
                self.request("/run-once", method="POST", secret=SECRET)
            )
        )
        first.start()
        self.assertTrue(worker.started.wait(timeout=5))

        status, body = self.request("/run-once", method="POST", secret=SECRET)
        self.assertEqual(status, HTTPStatus.CONFLICT)
        self.assertEqual(body, {"error": "worker_busy"})

        worker.release.set()
        first.join(timeout=5)
        self.assertEqual(first_result[0][0], HTTPStatus.OK)
        self.assertEqual(worker.calls, 1)

    def test_unknown_route_does_not_trigger_worker(self) -> None:
        status, _body = self.request("/unknown", method="POST", secret=SECRET)

        self.assertEqual(status, HTTPStatus.NOT_FOUND)
        self.assertEqual(self.worker.calls, 0)

    def test_run_once_does_not_accept_get(self) -> None:
        status, _body = self.request("/run-once", secret=SECRET)

        self.assertEqual(status, HTTPStatus.NOT_FOUND)
        self.assertEqual(self.worker.calls, 0)

    def test_short_secret_is_rejected_at_startup(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 32"):
            create_server(self.worker, "too-short", host="127.0.0.1", port=0)

    def test_background_poller_drains_jobs_until_queue_is_idle(self) -> None:
        worker = SequenceWorker()
        server = create_server(worker, SECRET, host="127.0.0.1", port=0)
        stopped = Event()
        poller = Thread(target=poll_queue, args=(server, stopped, 60))
        poller.start()
        self.assertTrue(worker.idle_reached.wait(timeout=5))
        stopped.set()
        poller.join(timeout=5)
        server.server_close()

        self.assertFalse(poller.is_alive())
        self.assertEqual(worker.calls, 2)


if __name__ == "__main__":
    unittest.main()
