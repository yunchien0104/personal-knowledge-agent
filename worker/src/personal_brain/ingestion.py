from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from threading import Event, Lock, Thread
from typing import Any, Protocol

from personal_brain.pdf_parser import PdfParseError, parse_native_pdf
from personal_brain.supabase_gateway import ClaimedJob, GatewayError


PROCESSOR_VERSION = "native-text-pypdf-v1"


class IngestionGateway(Protocol):
    def claim(self, worker_id: str, lease_seconds: int) -> ClaimedJob | None: ...
    def heartbeat(self, job: ClaimedJob, worker_id: str, lease_seconds: int) -> bool: ...
    def download_original(self, job: ClaimedJob) -> bytes: ...
    def upload_immutable(self, object_path: str, content: bytes, content_hash: str) -> None: ...
    def publish(self, job: ClaimedJob, worker_id: str, **result: Any) -> dict[str, Any] | None: ...
    def report_failure(self, job: ClaimedJob, worker_id: str, **failure: Any) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ProcessResult:
    status: str
    job_id: str | None = None
    revision_id: str | None = None
    note_id: str | None = None


class LeaseLostError(RuntimeError):
    pass


class _LeaseGuard:
    def __init__(
        self, gateway: IngestionGateway, job: ClaimedJob, worker_id: str, lease_seconds: int
    ) -> None:
        self.gateway = gateway
        self.job = job
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self._stop = Event()
        self._lost = Event()
        self._pulse_lock = Lock()
        self._thread = Thread(target=self._run, name=f"lease-{job.id}", daemon=True)

    def __enter__(self) -> _LeaseGuard:
        self.pulse()
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        interval = max(10.0, self.lease_seconds / 3)
        while not self._stop.wait(interval):
            try:
                self.pulse()
            except (GatewayError, LeaseLostError):
                self._lost.set()
                return

    def pulse(self) -> None:
        if self._lost.is_set():
            raise LeaseLostError("Worker lease is no longer valid")
        with self._pulse_lock:
            if not self.gateway.heartbeat(
                self.job, self.worker_id, self.lease_seconds
            ):
                self._lost.set()
                raise LeaseLostError("Worker lease is no longer valid")

    def ensure(self) -> None:
        if self._lost.is_set():
            raise LeaseLostError("Worker lease is no longer valid")


class PdfIngestionWorker:
    def __init__(
        self,
        gateway: IngestionGateway,
        worker_id: str,
        *,
        lease_seconds: int = 120,
        temporary_root: Path | None = None,
    ) -> None:
        if not 30 <= lease_seconds <= 600:
            raise ValueError("lease_seconds must be between 30 and 600")
        self.gateway = gateway
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.temporary_root = temporary_root

    def _report_failure(self, job: ClaimedJob, **failure: Any) -> None:
        try:
            self.gateway.report_failure(job, self.worker_id, **failure)
        except GatewayError:
            # The durable lease will expire and make the job reclaimable if the
            # failure report cannot reach the database.
            pass

    def process_one(self) -> ProcessResult:
        job = self.gateway.claim(self.worker_id, self.lease_seconds)
        if job is None:
            return ProcessResult("idle")

        try:
            with tempfile.TemporaryDirectory(
                prefix="personal-brain-pdf-", dir=self.temporary_root
            ) as directory:
                workdir = Path(directory)
                with _LeaseGuard(
                    self.gateway, job, self.worker_id, self.lease_seconds
                ) as lease:
                    source = self.gateway.download_original(job)
                    source_path = workdir / "source.pdf"
                    source_path.write_bytes(source)
                    lease.pulse()

                    parsed = parse_native_pdf(source_path.read_bytes())
                    artifact = json.dumps(
                        parsed.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                    artifact_hash = sha256(artifact).hexdigest()
                    artifact_path = workdir / "native-text.json"
                    artifact_path.write_bytes(artifact)
                    object_path = (
                        f"{job.user_id}/{job.asset_id}/revisions/{parsed.source_hash}/"
                        f"{PROCESSOR_VERSION}/native-text.json"
                    )
                    lease.pulse()
                    self.gateway.upload_immutable(
                        object_path, artifact_path.read_bytes(), artifact_hash
                    )
                    lease.pulse()
                    lease.ensure()

                publication = self.gateway.publish(
                    job,
                    self.worker_id,
                    source_hash=parsed.source_hash,
                    processor_version=PROCESSOR_VERSION,
                    object_path=object_path,
                    artifact_hash=artifact_hash,
                    artifact_size=len(artifact),
                    pages=[page for page in parsed.to_dict()["pages"]],
                )
                if publication is None:
                    raise LeaseLostError("Worker lease expired before publication")
                revision_id = publication.get("revisionId")
                if not isinstance(revision_id, str):
                    raise GatewayError(
                        "invalid_publication_response",
                        "Publication response is invalid",
                        retryable=True,
                    )
                return ProcessResult(
                    "completed", job.id, revision_id
                )
        except LeaseLostError:
            return ProcessResult("lease_lost", job.id)
        except PdfParseError as error:
            unsupported = error.code in {
                "blank_pdf", "encrypted_pdf", "scanned_pdf", "unsupported_pdf"
            }
            self._report_failure(
                job,
                error_class="unsupported" if unsupported else "permanent",
                error_code=error.code,
                error_message=str(error),
                retryable=False,
            )
            return ProcessResult("failed", job.id)
        except GatewayError as error:
            error_class = "transient" if error.retryable else "permanent"
            self._report_failure(
                job,
                error_class=error_class,
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
            )
            return ProcessResult("retry_wait" if error.retryable else "failed", job.id)
        except (OSError, ValueError, TypeError):
            self._report_failure(
                job,
                error_class="transient",
                error_code="worker_internal_error",
                error_message="Worker encountered an internal processing error",
                retryable=True,
            )
            return ProcessResult("retry_wait", job.id)
