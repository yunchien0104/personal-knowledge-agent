from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Protocol

from personal_brain.instagram_contract import (
    INSTAGRAM_IMAGE_MIME_TYPES,
    InstagramContractError,
    validate_instagram_post_contract,
)
from personal_brain.queue_runtime import LeaseGuard, LeaseLostError, ProcessResult
from personal_brain.supabase_gateway import ClaimedJob, GatewayError


class InstagramIngestionGateway(Protocol):
    def claim(self, worker_id: str, lease_seconds: int, resource_class: str) -> ClaimedJob | None: ...
    def heartbeat(self, job: ClaimedJob, worker_id: str, lease_seconds: int) -> bool: ...
    def download_instagram_contract(self, job: ClaimedJob) -> bytes: ...
    def download_instagram_media(self, job: ClaimedJob, media_id: str) -> bytes: ...
    def upload_immutable(
        self, object_path: str, content: bytes, content_hash: str,
        content_type: str = "application/json",
    ) -> None: ...
    def publish_instagram_post(
        self, job: ClaimedJob, worker_id: str, **result: Any
    ) -> dict[str, Any] | None: ...
    def report_failure(self, job: ClaimedJob, worker_id: str, **failure: Any) -> str | None: ...


def _matches_mime(content: bytes, mime: str) -> bool:
    if mime == "image/jpeg":
        return len(content) >= 4 and content[:3] == b"\xff\xd8\xff" and content[-2:] == b"\xff\xd9"
    if mime == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    return False


class InstagramPostIngestionWorker:
    def __init__(
        self,
        gateway: InstagramIngestionGateway,
        worker_id: str,
        *,
        lease_seconds: int = 120,
    ) -> None:
        if not 30 <= lease_seconds <= 600:
            raise ValueError("lease_seconds must be between 30 and 600")
        self.gateway = gateway
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def _failure(self, job: ClaimedJob, error: Exception) -> ProcessResult:
        if isinstance(error, GatewayError):
            retryable = error.retryable
            code = error.code
            message = str(error)
        else:
            retryable = False
            code = "invalid_instagram_contract"
            message = "Instagram Contract or media failed validation"
        try:
            self.gateway.report_failure(
                job,
                self.worker_id,
                error_class="transient" if retryable else "permanent",
                error_code=code,
                error_message=message,
                retryable=retryable,
            )
        except GatewayError:
            pass
        return ProcessResult("retry_wait" if retryable else "failed", job.id)

    def process_one(self) -> ProcessResult:
        job = self.gateway.claim(self.worker_id, self.lease_seconds, "media")
        if job is None:
            return ProcessResult("idle")
        if job.payload.job_type != "ingest_instagram_post":
            return self._failure(
                job,
                GatewayError("invalid_claim", "Worker received an unsupported Job", retryable=False),
            )
        try:
            with LeaseGuard(self.gateway, job, self.worker_id, self.lease_seconds) as lease:
                raw_contract = self.gateway.download_instagram_contract(job)
                if len(raw_contract) > 2 * 1024 * 1024:
                    raise InstagramContractError("Instagram contract is too large")
                try:
                    decoded = json.loads(raw_contract)
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise InstagramContractError("Instagram contract is not valid UTF-8 JSON") from error
                contract = validate_instagram_post_contract(decoded)
                if (
                    contract["source_url"] != job.payload.input.get("sourceUrl")
                    or contract["source_external_id"] != job.payload.input.get("sourceExternalId")
                ):
                    raise InstagramContractError("Instagram Job identity does not match the contract")

                source_hash = contract["source_hash"]
                processor_version = contract["provenance"]["processor_version"]
                prefix = (
                    f"{job.user_id}/{job.asset_id}/revisions/{source_hash}/"
                    f"{processor_version}"
                )
                canonical_artifact = json.dumps(
                    contract,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                artifact_hash = sha256(canonical_artifact).hexdigest()
                artifact_path = f"{prefix}/instagram-post.json"

                validated_media: list[tuple[dict[str, Any], bytes, str]] = []
                for media in contract["media"]:
                    media_id = media["id"]
                    mime = media["mime"]
                    if mime not in INSTAGRAM_IMAGE_MIME_TYPES:
                        raise InstagramContractError("Instagram media MIME is unsupported")
                    content = self.gateway.download_instagram_media(job, media_id)
                    content_hash = sha256(content).hexdigest()
                    if (
                        len(content) != media["size_bytes"]
                        or content_hash != media["sha256"]
                        or not _matches_mime(content, mime)
                    ):
                        raise InstagramContractError("Instagram image bytes do not match the contract")
                    validated_media.append((media, content, content_hash))
                    lease.pulse()

                lease.ensure()
                self.gateway.upload_immutable(artifact_path, canonical_artifact, artifact_hash)
                publications: list[dict[str, Any]] = []
                for media, content, content_hash in validated_media:
                    media_id = media["id"]
                    mime = media["mime"]
                    object_path = f"{prefix}/images/{media_id}"
                    lease.pulse()
                    self.gateway.upload_immutable(object_path, content, content_hash, mime)
                    publications.append({
                        "id": media_id,
                        "kind": "image",
                        "object_key": object_path,
                        "sha256": content_hash,
                        "mime": mime,
                        "size_bytes": len(content),
                    })
                lease.ensure()

            publication = self.gateway.publish_instagram_post(
                job,
                self.worker_id,
                source_hash=source_hash,
                processor_version=processor_version,
                object_path=artifact_path,
                artifact_hash=artifact_hash,
                artifact_size=len(canonical_artifact),
                media=publications,
                segments=contract["segments"],
            )
            if publication is None:
                raise LeaseLostError("Worker lease expired before publication")
            revision_id = publication.get("revisionId")
            if not isinstance(revision_id, str):
                raise GatewayError(
                    "invalid_publication_response",
                    "Instagram publication response is invalid",
                    retryable=True,
                )
            return ProcessResult("completed", job.id, revision_id)
        except LeaseLostError:
            return ProcessResult("lease_lost", job.id)
        except (InstagramContractError, GatewayError) as error:
            return self._failure(job, error)
        except (OSError, TypeError, ValueError) as error:
            return self._failure(
                job,
                GatewayError(
                    "instagram_worker_internal_error",
                    "Instagram Worker encountered an internal error",
                    retryable=True,
                ),
            )
