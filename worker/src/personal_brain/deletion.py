from __future__ import annotations

from typing import Any, Protocol

from personal_brain.queue_runtime import LeaseGuard, LeaseLostError, ProcessResult
from personal_brain.supabase_gateway import ClaimedJob, GatewayError, PDF_BUCKET


class DeletionGateway(Protocol):
    def claim(
        self, worker_id: str, lease_seconds: int, resource_class: str = "lightweight"
    ) -> ClaimedJob | None: ...
    def heartbeat(self, job: ClaimedJob, worker_id: str, lease_seconds: int) -> bool: ...
    def asset_deletion_context(
        self, job: ClaimedJob, worker_id: str
    ) -> dict[str, Any] | None: ...
    def list_storage_objects(self, prefix: str) -> list[str]: ...
    def remove_storage_objects(self, paths: list[str]) -> None: ...
    def complete_asset_deletion(
        self, job: ClaimedJob, worker_id: str
    ) -> dict[str, Any] | None: ...
    def report_failure(
        self, job: ClaimedJob, worker_id: str, **failure: Any
    ) -> str | None: ...


class AssetDeletionWorker:
    def __init__(
        self, gateway: DeletionGateway, worker_id: str, *, lease_seconds: int = 120
    ) -> None:
        if not 30 <= lease_seconds <= 600:
            raise ValueError("lease_seconds must be between 30 and 600")
        self.gateway = gateway
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def _report_failure(self, job: ClaimedJob, error: GatewayError) -> None:
        try:
            self.gateway.report_failure(
                job,
                self.worker_id,
                error_class="transient" if error.retryable else "permanent",
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
            )
        except GatewayError:
            pass

    def process_one(self) -> ProcessResult:
        job = self.gateway.claim(self.worker_id, self.lease_seconds, "deletion")
        if job is None:
            return ProcessResult("idle")
        try:
            with LeaseGuard(self.gateway, job, self.worker_id, self.lease_seconds) as lease:
                context = self.gateway.asset_deletion_context(job, self.worker_id)
                if context is None:
                    raise LeaseLostError("Deletion lease is no longer valid")
                asset_id = context.get("assetId")
                deletion_id = context.get("deletionId")
                mode = context.get("mode")
                prefix = context.get("storagePrefix")
                expected_prefix = f"{job.user_id}/{asset_id}"
                if mode == "remove_original":
                    expected_prefix += "/original"
                if (
                    context.get("storageBucket") != PDF_BUCKET
                    or asset_id != job.payload.input.get("assetId")
                    or deletion_id != job.payload.input.get("deletionId")
                    or mode != job.payload.input.get("mode")
                    or prefix != expected_prefix
                ):
                    raise GatewayError(
                        "invalid_deletion_context",
                        "Deletion context is inconsistent",
                        retryable=False,
                    )
                objects = self.gateway.list_storage_objects(prefix)
                lease.pulse()
                self.gateway.remove_storage_objects(objects)
                lease.pulse()
                lease.ensure()

            completed = self.gateway.complete_asset_deletion(job, self.worker_id)
            if completed is None or completed.get("status") != "completed":
                raise LeaseLostError("Deletion lease expired before completion")
            return ProcessResult("completed", job.id)
        except LeaseLostError:
            return ProcessResult("lease_lost", job.id)
        except GatewayError as error:
            self._report_failure(job, error)
            return ProcessResult("retry_wait" if error.retryable else "failed", job.id)
        except (OSError, TypeError, ValueError):
            self._report_failure(
                job,
                GatewayError(
                    "deletion_worker_error",
                    "Deletion worker encountered an internal error",
                    retryable=True,
                ),
            )
            return ProcessResult("retry_wait", job.id)
