from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Protocol

from personal_brain.instagram_contract import InstagramContractError
from personal_brain.instagram_ingestion import _matches_mime
from personal_brain.instagram_reel_contract import validate_instagram_reel_contract
from personal_brain.queue_runtime import LeaseGuard, LeaseLostError, ProcessResult
from personal_brain.supabase_gateway import ClaimedJob, GatewayError


class ReelGateway(Protocol):
    def claim(self, worker_id: str, lease_seconds: int, resource_class: str) -> ClaimedJob | None: ...
    def heartbeat(self, job: ClaimedJob, worker_id: str, lease_seconds: int) -> bool: ...
    def download_instagram_reel_contract(self, job: ClaimedJob) -> bytes: ...
    def download_instagram_reel_keyframe(self, job: ClaimedJob, media_id: str) -> bytes: ...
    def upload_immutable(self, object_path: str, content: bytes, content_hash: str, content_type: str = "application/json") -> None: ...
    def publish_instagram_reel(self, job: ClaimedJob, worker_id: str, **result: Any) -> dict[str, Any] | None: ...
    def report_failure(self, job: ClaimedJob, worker_id: str, **failure: Any) -> str | None: ...


class InstagramReelIngestionWorker:
    def __init__(self, gateway: ReelGateway, worker_id: str, *, lease_seconds: int = 120) -> None:
        if not 30 <= lease_seconds <= 600:
            raise ValueError("lease_seconds must be between 30 and 600")
        self.gateway, self.worker_id, self.lease_seconds = gateway, worker_id, lease_seconds

    def _failure(self, job: ClaimedJob, error: Exception) -> ProcessResult:
        retryable = isinstance(error, GatewayError) and error.retryable
        code = error.code if isinstance(error, GatewayError) else "invalid_instagram_reel_contract"
        try:
            self.gateway.report_failure(job, self.worker_id, error_class="transient" if retryable else "permanent",
                                        error_code=code, error_message=str(error)[:500], retryable=retryable)
        except GatewayError:
            pass
        return ProcessResult("retry_wait" if retryable else "failed", job.id)

    def process_one(self) -> ProcessResult:
        job = self.gateway.claim(self.worker_id, self.lease_seconds, "media")
        if job is None:
            return ProcessResult("idle")
        if job.payload.job_type != "ingest_instagram_reel":
            return self._failure(job, GatewayError("invalid_claim", "Worker received an unsupported Job", retryable=False))
        try:
            with LeaseGuard(self.gateway, job, self.worker_id, self.lease_seconds) as lease:
                raw = self.gateway.download_instagram_reel_contract(job)
                if len(raw) > 2 * 1024 * 1024:
                    raise InstagramContractError("Instagram Reel contract is too large")
                contract = validate_instagram_reel_contract(json.loads(raw))
                if contract["source_url"] != job.payload.input.get("sourceUrl") or contract["source_external_id"] != job.payload.input.get("sourceExternalId"):
                    raise InstagramContractError("Instagram Reel Job identity does not match contract")
                source_hash = contract["source_hash"]
                processor = contract["provenance"]["processor_version"]
                prefix = f"{job.user_id}/{job.asset_id}/revisions/{source_hash}/{processor}"
                artifact = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                artifact_hash = sha256(artifact).hexdigest()
                artifact_path = f"{prefix}/instagram-reel.json"
                frames: list[dict[str, Any]] = []
                for media in contract["media"]:
                    if media["kind"] != "keyframe":
                        continue
                    content = self.gateway.download_instagram_reel_keyframe(job, media["id"])
                    digest = sha256(content).hexdigest()
                    if len(content) != media["size_bytes"] or digest != media["sha256"] or not _matches_mime(content, media["mime"]):
                        raise InstagramContractError("Instagram Reel keyframe bytes do not match contract")
                    frames.append({"id": media["id"], "kind": "keyframe", "object_key": f"{prefix}/keyframes/{media['id']}",
                                   "sha256": digest, "mime": media["mime"], "size_bytes": len(content), "content": content})
                    lease.pulse()
                lease.ensure()
                self.gateway.upload_immutable(artifact_path, artifact, artifact_hash)
                publications = []
                for frame in frames:
                    content = frame.pop("content")
                    self.gateway.upload_immutable(frame["object_key"], content, frame["sha256"], frame["mime"])
                    publications.append(frame)
                    lease.pulse()
                lease.ensure()
            publication = self.gateway.publish_instagram_reel(job, self.worker_id, source_hash=source_hash,
                processor_version=processor, object_path=artifact_path, artifact_hash=artifact_hash,
                artifact_size=len(artifact), media=publications, segments=contract["segments"])
            if publication is None:
                raise LeaseLostError("Worker lease expired before publication")
            revision_id = publication.get("revisionId")
            if not isinstance(revision_id, str):
                raise GatewayError("invalid_publication_response", "Instagram Reel publication response is invalid", retryable=True)
            return ProcessResult("completed", job.id, revision_id)
        except LeaseLostError:
            return ProcessResult("lease_lost", job.id)
        except (InstagramContractError, GatewayError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return self._failure(job, error)
        except (OSError, TypeError, ValueError) as error:
            return self._failure(job, GatewayError("instagram_reel_worker_internal_error", "Instagram Reel Worker encountered an internal error", retryable=True))
