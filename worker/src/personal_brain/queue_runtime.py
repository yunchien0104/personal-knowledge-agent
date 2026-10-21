from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Protocol

from personal_brain.supabase_gateway import ClaimedJob, GatewayError


class LeaseGateway(Protocol):
    def heartbeat(
        self, job: ClaimedJob, worker_id: str, lease_seconds: int
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ProcessResult:
    status: str
    job_id: str | None = None
    revision_id: str | None = None
    note_id: str | None = None


class LeaseLostError(RuntimeError):
    pass


class LeaseGuard:
    def __init__(
        self, gateway: LeaseGateway, job: ClaimedJob, worker_id: str, lease_seconds: int
    ) -> None:
        self.gateway = gateway
        self.job = job
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self._stop = Event()
        self._lost = Event()
        self._pulse_lock = Lock()
        self._thread = Thread(target=self._run, name=f"lease-{job.id}", daemon=True)

    def __enter__(self) -> LeaseGuard:
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
