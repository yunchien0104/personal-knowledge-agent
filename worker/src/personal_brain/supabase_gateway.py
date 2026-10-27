from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from personal_brain.models import JobPayloadV1


PDF_BUCKET = "asset-private"


class GatewayError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    user_id: str
    asset_id: str | None
    lease_generation: int
    payload: JobPayloadV1
    created_at: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ClaimedJob:
        payload = JobPayloadV1.from_dict(row.get("payload_json"))
        job_id = str(row.get("id", ""))
        user_id = str(row.get("user_id", ""))
        raw_asset_id = row.get("asset_id")
        asset_id = raw_asset_id if isinstance(raw_asset_id, str) else None
        lease_generation = row.get("lease_generation")
        if payload.job_id != job_id or payload.user_id != user_id:
            raise GatewayError("invalid_claim", "Claimed Job identity is inconsistent", retryable=False)
        if payload.job_type not in {
            "ingest_pdf", "ingest_threads", "ingest_instagram_post", "ingest_instagram_reel", "generate_source_note", "execute_research", "rank_opportunities",
            "delete_asset", "index_retrieval",
        }:
            raise GatewayError("invalid_claim", "Claimed Job input is inconsistent", retryable=False)
        if payload.job_type in {"execute_research", "rank_opportunities", "delete_asset"}:
            required = {
                "execute_research": "researchRunId",
                "rank_opportunities": "rankingDate",
                "delete_asset": "deletionId",
            }[payload.job_type]
            if asset_id is not None or not isinstance(payload.input.get(required), str):
                raise GatewayError("invalid_claim", "Job input is inconsistent", retryable=False)
            if payload.job_type == "delete_asset" and (
                not isinstance(payload.input.get("assetId"), str)
                or payload.input.get("mode") not in {"remove_original", "purge_all"}
            ):
                raise GatewayError("invalid_claim", "Deletion input is inconsistent", retryable=False)
        elif payload.input.get("assetId") != asset_id:
            raise GatewayError("invalid_claim", "Claimed Job input is inconsistent", retryable=False)
        if payload.job_type in {"generate_source_note", "index_retrieval"} and not isinstance(
            payload.input.get("revisionId"), str
        ):
            raise GatewayError("invalid_claim", "Claimed Job Revision is invalid", retryable=False)
        if type(lease_generation) is not int or lease_generation < 1:
            raise GatewayError("invalid_claim", "Claimed Job lease is invalid", retryable=False)
        created_at = row.get("created_at")
        return cls(
            job_id,
            user_id,
            asset_id,
            lease_generation,
            payload,
            created_at if isinstance(created_at, str) else None,
        )


class SupabaseGateway:
    def __init__(self, base_url: str, service_key: str, *, timeout_seconds: int = 30) -> None:
        parsed = urlparse(base_url)
        local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme not in ({"http", "https"} if local else {"https"}) or not parsed.hostname:
            raise ValueError("SUPABASE_SERVER_URL is not trusted")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("SUPABASE_SERVER_URL must not contain credentials or query data")
        if not service_key or service_key.endswith("placeholder"):
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY is required")
        self._base_url = base_url.rstrip("/") + "/"
        self._service_key = service_key
        self._timeout_seconds = timeout_seconds
        self._opener = build_opener(_RejectRedirects())

    @classmethod
    def from_environment(cls) -> SupabaseGateway:
        return cls(
            os.environ.get("SUPABASE_SERVER_URL", ""),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/json",
        extra_headers: Mapping[str, str] | None = None,
    ) -> bytes:
        url = urljoin(self._base_url, path.lstrip("/"))
        if urlparse(url).netloc != urlparse(self._base_url).netloc:
            raise GatewayError("untrusted_url", "Supabase request target is not trusted", retryable=False)
        headers = {
            "apikey": self._service_key,
            "content-type": content_type,
        }
        if not self._service_key.startswith("sb_secret_"):
            headers["authorization"] = f"Bearer {self._service_key}"
        headers.update(extra_headers or {})
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                return response.read()
        except HTTPError as error:
            retryable = error.code == 429 or error.code >= 500
            raise GatewayError(
                "supabase_http_error",
                f"Supabase request failed with status {error.code}",
                retryable=retryable,
            ) from error
        except (TimeoutError, URLError) as error:
            raise GatewayError(
                "supabase_unavailable", "Supabase is temporarily unavailable", retryable=True
            ) from error

    def _rpc(self, name: str, arguments: Mapping[str, Any]) -> Any:
        raw = self._request(
            "POST",
            f"rest/v1/rpc/{quote(name, safe='')}",
            body=json.dumps(arguments, separators=(",", ":")).encode(),
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise GatewayError(
                "invalid_supabase_response", "Supabase returned an invalid response", retryable=True
            ) from error

    def claim(
        self, worker_id: str, lease_seconds: int, resource_class: str = "lightweight"
    ) -> ClaimedJob | None:
        rows = self._rpc(
            "claim_job",
            {
                "p_worker_id": worker_id,
                "p_resource_class": resource_class,
                "p_lease_seconds": lease_seconds,
            },
        )
        if not rows:
            return None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise GatewayError("invalid_claim", "Queue claim response is invalid", retryable=True)
        return ClaimedJob.from_row(rows[0])

    def heartbeat(self, job: ClaimedJob, worker_id: str, lease_seconds: int) -> bool:
        return self._rpc(
            "heartbeat_job",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_lease_seconds": lease_seconds,
            },
        ) is True

    def report_failure(
        self,
        job: ClaimedJob,
        worker_id: str,
        *,
        error_class: str,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> str | None:
        result = self._rpc(
            "report_job_failure",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_error_class": error_class,
                "p_error_code": error_code[:128],
                "p_error_message": error_message[:500],
                "p_retryable": retryable,
            },
        )
        return result if isinstance(result, str) else None

    def download_original(self, job: ClaimedJob) -> bytes:
        bucket = job.payload.input.get("storageBucket")
        object_path = job.payload.input.get("objectPath")
        expected = f"{job.user_id}/{job.asset_id}/original/source.pdf"
        if bucket != PDF_BUCKET or object_path != expected:
            raise GatewayError("invalid_storage_path", "Job Storage path is invalid", retryable=False)
        return self._request(
            "GET", f"storage/v1/object/{PDF_BUCKET}/{quote(expected, safe='/')}"
        )

    def download_instagram_contract(self, job: ClaimedJob) -> bytes:
        expected = f"{job.user_id}/{job.asset_id}/original/instagram-contract.json"
        if (
            job.payload.input.get("storageBucket") != PDF_BUCKET
            or job.payload.input.get("contractObjectPath") != expected
        ):
            raise GatewayError(
                "invalid_storage_path", "Instagram Contract path is invalid", retryable=False
            )
        return self._request(
            "GET", f"storage/v1/object/{PDF_BUCKET}/{quote(expected, safe='/')}"
        )

    def download_instagram_media(self, job: ClaimedJob, media_id: str) -> bytes:
        if not media_id or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for character in media_id):
            raise GatewayError(
                "invalid_storage_path", "Instagram media identity is invalid", retryable=False
            )
        prefix = f"{job.user_id}/{job.asset_id}/original/images"
        if job.payload.input.get("mediaPrefix") != prefix:
            raise GatewayError(
                "invalid_storage_path", "Instagram media prefix is invalid", retryable=False
            )
        expected = f"{prefix}/{media_id}"
        return self._request(
            "GET", f"storage/v1/object/{PDF_BUCKET}/{quote(expected, safe='/')}"
        )

    def download_instagram_reel_contract(self, job: ClaimedJob) -> bytes:
        expected = f"{job.user_id}/{job.asset_id}/original/instagram-reel-contract.json"
        if job.payload.input.get("storageBucket") != PDF_BUCKET or job.payload.input.get("contractObjectPath") != expected:
            raise GatewayError("invalid_storage_path", "Instagram Reel Contract path is invalid", retryable=False)
        return self._request("GET", f"storage/v1/object/{PDF_BUCKET}/{quote(expected, safe='/')}")

    def download_instagram_reel_keyframe(self, job: ClaimedJob, media_id: str) -> bytes:
        if not media_id or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for character in media_id):
            raise GatewayError("invalid_storage_path", "Instagram Reel keyframe identity is invalid", retryable=False)
        prefix = f"{job.user_id}/{job.asset_id}/original/keyframes"
        if job.payload.input.get("mediaPrefix") != prefix:
            raise GatewayError("invalid_storage_path", "Instagram Reel keyframe prefix is invalid", retryable=False)
        return self._request("GET", f"storage/v1/object/{PDF_BUCKET}/{quote(f'{prefix}/{media_id}', safe='/')}")

    def source_note_context(self, job: ClaimedJob, worker_id: str) -> dict[str, Any] | None:
        source_type = job.payload.input.get("sourceType")
        function_name = {
            "instagram_post": "get_instagram_post_source_note_context",
            "instagram_reel": "get_instagram_reel_source_note_context",
        }.get(source_type, "get_source_note_context")
        result = self._rpc(
            function_name,
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
            },
        )
        return result if isinstance(result, dict) else None

    def topic_classification_options(self, user_id: str) -> list[dict[str, Any]]:
        result = self._rpc("get_topic_classification_options", {"p_user_id": user_id})
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise GatewayError(
                "invalid_topic_options", "Topic options response is invalid", retryable=True
            )
        return result

    def apply_ai_note_topics(
        self,
        note_id: str,
        topic_ids: list[str],
        status: str,
        error: str | None,
    ) -> dict[str, Any] | None:
        result = self._rpc(
            "apply_ai_note_topics",
            {
                "p_note_id": note_id,
                "p_topic_ids": topic_ids,
                "p_status": status,
                "p_error": error,
            },
        )
        return result if isinstance(result, dict) else None

    def topic_backfill_candidates(self, limit: int) -> list[dict[str, Any]]:
        result = self._rpc("get_topic_backfill_candidates", {"p_limit": limit})
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise GatewayError(
                "invalid_backfill_candidates", "Topic backfill response is invalid", retryable=True
            )
        return result

    def download_artifact(self, object_path: str) -> bytes:
        if not object_path or ".." in object_path.split("/"):
            raise GatewayError("invalid_storage_path", "Artifact path is invalid", retryable=False)
        return self._request(
            "GET", f"storage/v1/object/{PDF_BUCKET}/{quote(object_path, safe='/')}"
        )

    def asset_deletion_context(
        self, job: ClaimedJob, worker_id: str
    ) -> dict[str, Any] | None:
        result = self._rpc(
            "get_asset_deletion_context",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
            },
        )
        return result if isinstance(result, dict) else None

    def list_storage_objects(self, prefix: str) -> list[str]:
        if not prefix or ".." in prefix.split("/"):
            raise GatewayError("invalid_storage_path", "Storage prefix is invalid", retryable=False)
        pending = [prefix.rstrip("/")]
        objects: list[str] = []
        while pending:
            current = pending.pop()
            offset = 0
            while True:
                raw = self._request(
                    "POST",
                    f"storage/v1/object/list/{PDF_BUCKET}",
                    body=json.dumps({
                        "prefix": current,
                        "limit": 100,
                        "offset": offset,
                        "sortBy": {"column": "name", "order": "asc"},
                    }, separators=(",", ":")).encode(),
                )
                try:
                    rows = json.loads(raw)
                except json.JSONDecodeError as error:
                    raise GatewayError(
                        "invalid_supabase_response",
                        "Storage returned an invalid listing",
                        retryable=True,
                    ) from error
                if not isinstance(rows, list):
                    raise GatewayError(
                        "invalid_supabase_response",
                        "Storage returned an invalid listing",
                        retryable=True,
                    )
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                        raise GatewayError(
                            "invalid_supabase_response",
                            "Storage returned an invalid object",
                            retryable=True,
                        )
                    path = f"{current}/{row['name']}"
                    if path != prefix and not path.startswith(prefix.rstrip("/") + "/"):
                        raise GatewayError(
                            "invalid_storage_path", "Storage listing escaped its prefix", retryable=False
                        )
                    if row.get("id") is None:
                        pending.append(path)
                    else:
                        objects.append(path)
                    if len(objects) + len(pending) > 10000:
                        raise GatewayError(
                            "storage_listing_too_large",
                            "Asset Storage listing exceeds the deletion limit",
                            retryable=False,
                        )
                if len(rows) < 100:
                    break
                offset += len(rows)
        return objects

    def remove_storage_objects(self, paths: list[str]) -> None:
        for offset in range(0, len(paths), 100):
            self._request(
                "DELETE",
                f"storage/v1/object/{PDF_BUCKET}",
                body=json.dumps({"prefixes": paths[offset:offset + 100]}, separators=(",", ":")).encode(),
            )

    def complete_asset_deletion(
        self, job: ClaimedJob, worker_id: str
    ) -> dict[str, Any] | None:
        result = self._rpc(
            "complete_asset_deletion",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
            },
        )
        return result if isinstance(result, dict) else None

    def upload_immutable(
        self,
        object_path: str,
        content: bytes,
        content_hash: str,
        content_type: str = "application/json",
    ) -> None:
        path = f"storage/v1/object/{PDF_BUCKET}/{quote(object_path, safe='/')}"
        try:
            self._request(
                "POST",
                path,
                body=content,
                content_type=content_type,
                extra_headers={"x-upsert": "false", "cache-control": "max-age=31536000"},
            )
            return
        except GatewayError as error:
            cause = error.__cause__
            if not isinstance(cause, HTTPError) or cause.code not in {400, 409}:
                raise
        existing = self._request("GET", path)
        from hashlib import sha256

        if sha256(existing).hexdigest() != content_hash:
            raise GatewayError(
                "artifact_conflict", "Immutable artifact content conflicts", retryable=False
            )

    def publish(
        self,
        job: ClaimedJob,
        worker_id: str,
        *,
        source_hash: str,
        processor_version: str,
        object_path: str,
        artifact_hash: str,
        artifact_size: int,
        pages: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        result = self._rpc(
            "publish_pdf_ingestion",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_source_hash": source_hash,
                "p_processor_version": processor_version,
                "p_object_key": object_path,
                "p_artifact_sha256": artifact_hash,
                "p_artifact_size_bytes": artifact_size,
                "p_pages": pages,
            },
        )
        return result if isinstance(result, dict) else None

    def publish_threads(
        self,
        job: ClaimedJob,
        worker_id: str,
        *,
        source_hash: str,
        processor_version: str,
        object_path: str,
        artifact_hash: str,
        artifact_size: int,
        segments: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        result = self._rpc(
            "publish_threads_ingestion",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_source_hash": source_hash,
                "p_processor_version": processor_version,
                "p_object_key": object_path,
                "p_artifact_sha256": artifact_hash,
                "p_artifact_size_bytes": artifact_size,
                "p_segments": segments,
            },
        )
        return result if isinstance(result, dict) else None

    def publish_instagram_post(
        self,
        job: ClaimedJob,
        worker_id: str,
        *,
        source_hash: str,
        processor_version: str,
        object_path: str,
        artifact_hash: str,
        artifact_size: int,
        media: list[dict[str, Any]],
        segments: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        result = self._rpc(
            "publish_instagram_post_ingestion",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_source_hash": source_hash,
                "p_processor_version": processor_version,
                "p_object_key": object_path,
                "p_artifact_sha256": artifact_hash,
                "p_artifact_size_bytes": artifact_size,
                "p_media": media,
                "p_segments": segments,
            },
        )
        return result if isinstance(result, dict) else None

    def publish_instagram_reel(
        self, job: ClaimedJob, worker_id: str, *, source_hash: str,
        processor_version: str, object_path: str, artifact_hash: str,
        artifact_size: int, media: list[dict[str, Any]], segments: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        result = self._rpc("publish_instagram_reel_ingestion", {
            "p_job_id": job.id, "p_worker_id": worker_id,
            "p_lease_generation": job.lease_generation, "p_source_hash": source_hash,
            "p_processor_version": processor_version, "p_object_key": object_path,
            "p_artifact_sha256": artifact_hash, "p_artifact_size_bytes": artifact_size,
            "p_media": media, "p_segments": segments,
        })
        return result if isinstance(result, dict) else None

    def publish_source_note(
        self,
        job: ClaimedJob,
        worker_id: str,
        *,
        model_provider: str,
        model_name: str,
        prompt_version: str,
        output_hash: str,
        title: str,
        body: str,
        claims: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        result = self._rpc(
            "publish_source_note",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_model_provider": model_provider,
                "p_model_name": model_name,
                "p_prompt_version": prompt_version,
                "p_output_hash": output_hash,
                "p_title": title,
                "p_body": body,
                "p_claims": claims,
            },
        )
        return result if isinstance(result, dict) else None

    def research_context(
        self,
        job: ClaimedJob,
        worker_id: str,
        plan: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        result = self._rpc(
            "get_research_context",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_plan": plan,
            },
        )
        return result if isinstance(result, dict) else None

    def publish_research_draft(
        self,
        job: ClaimedJob,
        worker_id: str,
        *,
        model_provider: str,
        model_name: str,
        prompt_version: str,
        output_hash: str,
        title: str,
        body: str,
        findings: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        result = self._rpc(
            "publish_research_draft",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_model_provider": model_provider,
                "p_model_name": model_name,
                "p_prompt_version": prompt_version,
                "p_output_hash": output_hash,
                "p_title": title,
                "p_body": body,
                "p_findings": findings,
            },
        )
        return result if isinstance(result, dict) else None

    def opportunity_context(
        self, job: ClaimedJob, worker_id: str
    ) -> dict[str, Any] | None:
        result = self._rpc(
            "get_opportunity_context",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
            },
        )
        return result if isinstance(result, dict) else None

    def enqueue_due_opportunities(self) -> int:
        result = self._rpc("enqueue_due_opportunity_rankings", {})
        if type(result) is not int or result < 0:
            raise GatewayError(
                "invalid_opportunity_schedule",
                "Opportunity schedule result is invalid",
                retryable=False,
            )
        return result

    def publish_opportunities(
        self,
        job: ClaimedJob,
        worker_id: str,
        *,
        candidates: list[dict[str, Any]],
        estimated_cost_usd: str,
    ) -> dict[str, Any] | None:
        result = self._rpc(
            "publish_opportunity_ranking",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_candidates": candidates,
                "p_estimated_cost_usd": estimated_cost_usd,
            },
        )
        return result if isinstance(result, dict) else None

    def pause_opportunity_budget(self, job: ClaimedJob, worker_id: str) -> bool:
        return self._rpc(
            "pause_opportunity_budget",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
            },
        ) is True
