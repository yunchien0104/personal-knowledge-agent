from __future__ import annotations

from dataclasses import asdict
import json
import logging
import os
import secrets
import signal
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from typing import Any, Mapping, Protocol

from personal_brain.ingestion import ProcessResult
from personal_brain.runner import build_queue_worker


LOGGER = logging.getLogger(__name__)
MINIMUM_SECRET_LENGTH = 32
MAX_REQUEST_BODY_BYTES = 1024


class QueueWorker(Protocol):
    def process_one(self) -> ProcessResult: ...


class WorkerHttpServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True

    def __init__(
        self,
        server_address: tuple[str, int],
        worker: QueueWorker,
        run_once_secret: str,
    ) -> None:
        super().__init__(server_address, WorkerRequestHandler)
        self.worker = worker
        self.run_once_secret = run_once_secret
        self.run_lock = Lock()


class WorkerRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PersonalBrainWorker"
    sys_version = ""

    @property
    def worker_server(self) -> WorkerHttpServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, message: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), message % args)

    def _send_json(
        self,
        status: HTTPStatus,
        payload: Mapping[str, Any],
        *,
        authenticate: bool = False,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if authenticate:
            self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _authorized(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        supplied = authorization[len(prefix):] if authorization.startswith(prefix) else ""
        return secrets.compare_digest(supplied, self.worker_server.run_once_secret)

    def _consume_bounded_body(self) -> bool:
        if self.headers.get("Transfer-Encoding") is not None:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return False
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return False
        if length < 0 or length > MAX_REQUEST_BODY_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            return False
        if length:
            self.rfile.read(length)
        return True

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/run-once":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._authorized():
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "unauthorized"},
                authenticate=True,
            )
            return
        if not self._consume_bounded_body():
            return
        if not self.worker_server.run_lock.acquire(blocking=False):
            self._send_json(HTTPStatus.CONFLICT, {"error": "worker_busy"})
            return
        try:
            result = self.worker_server.worker.process_one()
            self._send_json(HTTPStatus.OK, asdict(result))
        except Exception as error:
            LOGGER.error("run-once failed: %s", type(error).__name__)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "worker_error"})
        finally:
            self.worker_server.run_lock.release()


def _validated_secret(environ: Mapping[str, str]) -> str:
    secret = environ.get("WORKER_RUN_ONCE_SECRET", "")
    if len(secret) < MINIMUM_SECRET_LENGTH:
        raise ValueError(
            f"WORKER_RUN_ONCE_SECRET must contain at least {MINIMUM_SECRET_LENGTH} characters"
        )
    return secret


def _validated_port(environ: Mapping[str, str]) -> int:
    try:
        port = int(environ.get("PORT", "10000"))
    except ValueError as error:
        raise ValueError("PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    return port


def create_server(
    worker: QueueWorker,
    run_once_secret: str,
    *,
    host: str = "0.0.0.0",
    port: int = 10000,
) -> WorkerHttpServer:
    if len(run_once_secret) < MINIMUM_SECRET_LENGTH:
        raise ValueError(
            f"run-once secret must contain at least {MINIMUM_SECRET_LENGTH} characters"
        )
    return WorkerHttpServer((host, port), worker, run_once_secret)


def serve() -> int:
    try:
        run_once_secret = _validated_secret(os.environ)
        port = _validated_port(os.environ)
        worker = build_queue_worker()
        server = create_server(
            worker,
            run_once_secret,
            port=port,
        )
    except (OSError, ValueError) as error:
        print(f"worker web error: {error}", file=sys.stderr)
        return 2

    def request_shutdown(_signum: int, _frame: object) -> None:
        Thread(target=server.shutdown, name="worker-http-shutdown", daemon=True).start()

    previous_handlers = {
        signum: signal.signal(signum, request_shutdown)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        server.serve_forever()
    finally:
        server.server_close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0


def cli() -> None:
    raise SystemExit(serve())


if __name__ == "__main__":
    cli()
