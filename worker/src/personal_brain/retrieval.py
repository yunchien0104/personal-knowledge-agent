from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
import re
import time
from typing import Any, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from personal_brain.text_normalization import lexical_search_text, normalize_for_retrieval


EMBEDDING_DIMENSIONS = 1536
_BOUNDARY = re.compile(r"\n\n|\n|[。！？.!?](?:\s+|$)")


class RetrievalError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class RetrievalChunk:
    chunk_index: int
    evidence_id: str
    content: str
    search_text: str
    content_hash: str
    start_offset: int
    end_offset: int
    locator: Mapping[str, Any]

    def publication(self, embedding: Sequence[float]) -> dict[str, Any]:
        return {
            "chunkIndex": self.chunk_index,
            "evidenceId": self.evidence_id,
            "content": self.content,
            "searchText": self.search_text,
            "contentHash": self.content_hash,
            "startOffset": self.start_offset,
            "endOffset": self.end_offset,
            "locator": dict(self.locator),
            "embedding": list(embedding),
        }


def _integer(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise RetrievalError("invalid_normalized_artifact", f"{name} is invalid", retryable=False)
    return value


def _page_evidence(
    page: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], source_hash: str
) -> Mapping[str, Any]:
    page_number = _integer(page.get("page_number"), "page number")
    start = _integer(page.get("start_offset"), "page start offset")
    end = _integer(page.get("end_offset"), "page end offset")
    matches: list[Mapping[str, Any]] = []
    for row in evidence:
        locator = row.get("locator")
        if not isinstance(locator, Mapping):
            continue
        if (
            row.get("sourceHash") == source_hash
            and locator.get("kind") == "pdf_page"
            and locator.get("page_number") == page_number
            and locator.get("start_offset") == start
            and locator.get("end_offset") == end
            and locator.get("source_hash") == source_hash
        ):
            matches.append(row)
    if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
        raise RetrievalError(
            "invalid_evidence_mapping",
            "Normalized page does not map to exactly one validated Evidence row",
            retryable=False,
        )
    return matches[0]


def _chunk_spans(text: str, max_characters: int, overlap_characters: int) -> list[tuple[int, int]]:
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + max_characters)
        end = hard_end
        if hard_end < len(text):
            minimum = start + max(1, max_characters // 2)
            boundaries = [match.end() for match in _BOUNDARY.finditer(text, minimum, hard_end)]
            if boundaries:
                end = boundaries[-1]
        content_start = start
        while content_start < end and text[content_start].isspace():
            content_start += 1
        content_end = end
        while content_end > content_start and text[content_end - 1].isspace():
            content_end -= 1
        if content_end > content_start:
            spans.append((content_start, content_end))
        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap_characters)
        start = next_start
    return spans


def chunk_normalized_pdf(
    artifact: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    *,
    max_characters: int = 1200,
    overlap_characters: int = 180,
) -> list[RetrievalChunk]:
    """Chunk full normalized pages and retain a validated page-Evidence mapping."""

    if not 200 <= max_characters <= 4000:
        raise ValueError("max_characters must be between 200 and 4000")
    if not 0 <= overlap_characters < max_characters // 2:
        raise ValueError("overlap_characters must be less than half the chunk size")
    source_hash = artifact.get("source_hash")
    pages = artifact.get("pages")
    full_text = artifact.get("text")
    if (
        artifact.get("schema_version") not in {1, 2}
        or not isinstance(source_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", source_hash) is None
        or not isinstance(pages, list)
        or not isinstance(full_text, str)
    ):
        raise RetrievalError("invalid_normalized_artifact", "Normalized PDF is invalid", retryable=False)

    chunks: list[RetrievalChunk] = []
    reconstructed: list[str] = []
    previous_end = 0
    for page_index, page in enumerate(pages):
        if not isinstance(page, Mapping) or not isinstance(page.get("text"), str):
            raise RetrievalError("invalid_normalized_artifact", "Normalized page is invalid", retryable=False)
        text = page["text"]
        retrieval_text = page.get("retrieval_text", text)
        if not isinstance(retrieval_text, str):
            raise RetrievalError("invalid_normalized_artifact", "Retrieval text is invalid", retryable=False)
        start_offset = _integer(page.get("start_offset"), "page start offset")
        end_offset = _integer(page.get("end_offset"), "page end offset")
        if (
            page.get("page_number") != page_index + 1
            or page.get("source_hash") != source_hash
            or start_offset != previous_end + (0 if page_index == 0 else 2)
            or end_offset != start_offset + len(text)
        ):
            raise RetrievalError("invalid_normalized_artifact", "Normalized page offsets are invalid", retryable=False)
        mapped = _page_evidence(page, evidence, source_hash)
        locator = mapped.get("locator")
        assert isinstance(locator, Mapping)
        for local_start, local_end in _chunk_spans(text, max_characters, overlap_characters):
            content = text[local_start:local_end]
            chunks.append(RetrievalChunk(
                chunk_index=len(chunks),
                evidence_id=str(mapped["id"]),
                content=content,
                search_text=lexical_search_text(normalize_for_retrieval(content)),
                content_hash=sha256(content.encode()).hexdigest(),
                start_offset=start_offset + local_start,
                end_offset=start_offset + local_end,
                locator=dict(locator),
            ))
        reconstructed.append(text)
        previous_end = end_offset
    if "\n\n".join(reconstructed) != full_text or not chunks:
        raise RetrievalError("invalid_normalized_artifact", "Normalized document text is inconsistent", retryable=False)
    return chunks


@dataclass(frozen=True, slots=True)
class EmbeddingSettings:
    base_url: str
    api_key: str
    model: str = "text-embedding-3-small"
    version: str = "openai-v1-1536"
    dimensions: int = EMBEDDING_DIMENSIONS
    batch_size: int = 32
    timeout_seconds: int = 30
    max_retries: int = 2

    @classmethod
    def from_environment(cls) -> EmbeddingSettings:
        return cls(
            base_url=os.environ.get("EMBEDDING_API_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.environ.get("EMBEDDING_API_KEY", ""),
            model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
            version=os.environ.get("EMBEDDING_MODEL_VERSION", "openai-v1-1536"),
            dimensions=int(os.environ.get("EMBEDDING_DIMENSIONS", "1536")),
            batch_size=int(os.environ.get("EMBEDDING_BATCH_SIZE", "32")),
            timeout_seconds=int(os.environ.get("EMBEDDING_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.environ.get("EMBEDDING_MAX_RETRIES", "2")),
        )


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class OpenAICompatibleEmbeddingProvider:
    """Bounded OpenAI-compatible adapter; configuration is separate from Sol."""

    def __init__(self, settings: EmbeddingSettings) -> None:
        parsed = urlparse(settings.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("EMBEDDING_API_BASE_URL must be a trusted HTTPS origin")
        if not settings.api_key or not settings.model or not settings.version:
            raise ValueError("Embedding provider configuration is incomplete")
        if settings.dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError("EMBEDDING_DIMENSIONS must be 1536 for the R1 schema")
        if not 1 <= settings.batch_size <= 100:
            raise ValueError("EMBEDDING_BATCH_SIZE must be between 1 and 100")
        if not 1 <= settings.timeout_seconds <= 60 or not 0 <= settings.max_retries <= 5:
            raise ValueError("Embedding timeout or retry configuration is invalid")
        self.settings = settings
        self._url = settings.base_url.rstrip("/") + "/embeddings"
        self._opener = build_opener(_RejectRedirects())
        self._cache: dict[tuple[str, str, str], list[float]] = {}

    def _request_batch(self, inputs: Sequence[str]) -> list[list[float]]:
        body = json.dumps({
            "model": self.settings.model,
            "input": list(inputs),
            "dimensions": self.settings.dimensions,
        }, separators=(",", ":")).encode()
        for attempt in range(self.settings.max_retries + 1):
            request = Request(self._url, data=body, method="POST", headers={
                "authorization": f"Bearer {self.settings.api_key}",
                "content-type": "application/json",
            })
            try:
                with self._opener.open(request, timeout=self.settings.timeout_seconds) as response:
                    payload = json.loads(response.read())
                rows = payload.get("data") if isinstance(payload, Mapping) else None
                if (
                    not isinstance(payload, Mapping)
                    or payload.get("model") != self.settings.model
                    or not isinstance(rows, list)
                    or len(rows) != len(inputs)
                ):
                    raise RetrievalError("invalid_embedding_response", "Embedding response is invalid", retryable=True)
                ordered = sorted(rows, key=lambda row: row.get("index", -1) if isinstance(row, Mapping) else -1)
                vectors: list[list[float]] = []
                for expected, row in enumerate(ordered):
                    vector = row.get("embedding") if isinstance(row, Mapping) else None
                    if (
                        not isinstance(row, Mapping)
                        or row.get("index") != expected
                        or not isinstance(vector, list)
                        or len(vector) != EMBEDDING_DIMENSIONS
                    ):
                        raise RetrievalError("invalid_embedding_response", "Embedding vector is invalid", retryable=True)
                    converted = [float(value) for value in vector]
                    if not all(math.isfinite(value) for value in converted) or not any(value != 0 for value in converted):
                        raise RetrievalError("invalid_embedding_response", "Embedding vector is invalid", retryable=True)
                    vectors.append(converted)
                return vectors
            except RetrievalError as error:
                if not error.retryable or attempt >= self.settings.max_retries:
                    raise
            except HTTPError as error:
                retryable = error.code == 429 or error.code >= 500
                if not retryable or attempt >= self.settings.max_retries:
                    raise RetrievalError(
                        "embedding_provider_error", f"Embedding provider returned status {error.code}",
                        retryable=retryable,
                    ) from error
            except (TimeoutError, URLError, json.JSONDecodeError, TypeError, ValueError) as error:
                if attempt >= self.settings.max_retries:
                    raise RetrievalError(
                        "embedding_provider_unavailable", "Embedding provider is unavailable", retryable=True
                    ) from error
            time.sleep(min(2 ** attempt, 4))
        raise AssertionError("unreachable")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding input must contain non-empty text")
        results: list[list[float] | None] = [None] * len(texts)
        missing: dict[tuple[str, str, str], tuple[str, list[int]]] = {}
        for index, text in enumerate(texts):
            key = (sha256(text.encode()).hexdigest(), self.settings.model, self.settings.version)
            cached = self._cache.get(key)
            if cached is None:
                if key not in missing:
                    missing[key] = (text, [])
                missing[key][1].append(index)
            else:
                results[index] = cached
        pending = list(missing.items())
        for offset in range(0, len(pending), self.settings.batch_size):
            batch = pending[offset:offset + self.settings.batch_size]
            vectors = self._request_batch([item[1][0] for item in batch])
            for (key, (_text, indexes)), vector in zip(batch, vectors, strict=True):
                self._cache[key] = vector
                for index in indexes:
                    results[index] = vector
        return [list(vector) for vector in results if vector is not None]


class RetrievalGateway(Protocol):
    def download_artifact(self, object_path: str) -> bytes: ...
    def replace_retrieval_chunks(self, context: Mapping[str, Any], model: str,
                                 version: str, chunks: list[dict[str, Any]]) -> int: ...
    def hybrid_retrieve(self, user_id: str, query: str, query_embedding: list[float] | None,
                        model: str, version: str, scope_asset_ids: list[str] | None,
                        top_k: int, candidate_limit: int) -> Mapping[str, Any]: ...


class RetrievalIndexer:
    def __init__(self, gateway: RetrievalGateway, provider: OpenAICompatibleEmbeddingProvider) -> None:
        self.gateway = gateway
        self.provider = provider

    def build_publications(self, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        object_path = context.get("artifactObjectKey")
        evidence = context.get("evidence")
        if not isinstance(object_path, str) or not isinstance(evidence, list):
            raise RetrievalError("invalid_backfill_context", "Retrieval context is invalid", retryable=False)
        try:
            artifact = json.loads(self.gateway.download_artifact(object_path))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RetrievalError("invalid_normalized_artifact", "Normalized artifact is invalid", retryable=False) from error
        if not isinstance(artifact, Mapping) or artifact.get("source_hash") != context.get("sourceFingerprint"):
            raise RetrievalError("invalid_normalized_artifact", "Artifact fingerprint is invalid", retryable=False)
        chunks = chunk_normalized_pdf(artifact, evidence)
        vectors = self.provider.embed([chunk.search_text for chunk in chunks])
        return [chunk.publication(vector) for chunk, vector in zip(chunks, vectors, strict=True)]

    def index_context(self, context: Mapping[str, Any]) -> int:
        publications = self.build_publications(context)
        return self.gateway.replace_retrieval_chunks(
            context, self.provider.settings.model, self.provider.settings.version, publications
        )


class RetrievalIndexWorker:
    """Durable, leased indexing worker using the shared Jobs queue."""

    def __init__(self, gateway: Any, provider: OpenAICompatibleEmbeddingProvider,
                 worker_id: str, *, lease_seconds: int = 120) -> None:
        self.gateway = gateway
        self.provider = provider
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def process_one(self):
        from personal_brain.queue_runtime import LeaseGuard, LeaseLostError, ProcessResult
        from personal_brain.supabase_gateway import GatewayError

        self.gateway.enqueue_retrieval_index_jobs(
            self.provider.settings.model, self.provider.settings.version, limit=25
        )
        job = self.gateway.claim(self.worker_id, self.lease_seconds, "retrieval")
        if job is None:
            return ProcessResult("idle")
        try:
            if job.payload.job_type != "index_retrieval":
                raise GatewayError("invalid_claim", "Worker received an unsupported Job", retryable=False)
            with LeaseGuard(self.gateway, job, self.worker_id, self.lease_seconds) as lease:
                context = self.gateway.retrieval_index_context(job, self.worker_id)
                if context is None:
                    raise LeaseLostError("Worker lease is no longer valid")
                publications = RetrievalIndexer(self.gateway, self.provider).build_publications(context)
                lease.pulse()
                result = self.gateway.publish_retrieval_index(
                    job, self.worker_id,
                    embedding_model=self.provider.settings.model,
                    embedding_version=self.provider.settings.version,
                    chunks=publications,
                )
                if result is None:
                    raise LeaseLostError("Worker lease expired before indexing publication")
            return ProcessResult("completed", job.id, revision_id=str(context["revisionId"]))
        except LeaseLostError:
            return ProcessResult("lease_lost", job.id)
        except (RetrievalError, GatewayError) as error:
            self.gateway.report_failure(
                job, self.worker_id,
                error_class="transient" if error.retryable else "permanent",
                error_code=error.code, error_message=str(error), retryable=error.retryable,
            )
            return ProcessResult("retry_wait" if error.retryable else "failed", job.id)


class HybridRetriever:
    def __init__(self, gateway: RetrievalGateway, provider: OpenAICompatibleEmbeddingProvider) -> None:
        self.gateway = gateway
        self.provider = provider

    def retrieve(
        self, user_id: str, query: str, *, scope_asset_ids: list[str] | None = None,
        top_k: int = 12, candidate_limit: int = 40,
    ) -> Mapping[str, Any]:
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 500:
            raise ValueError("query must contain between 2 and 500 characters")
        if not 1 <= top_k <= candidate_limit <= 200:
            raise ValueError("retrieval limits are invalid")
        if scope_asset_ids is not None and not 1 <= len(scope_asset_ids) <= 100:
            raise ValueError("explicit retrieval scope must contain between 1 and 100 assets")
        try:
            query_embedding = self.provider.embed([query])[0]
        except RetrievalError:
            query_embedding = None
        result = self.gateway.hybrid_retrieve(
            user_id, query, query_embedding, self.provider.settings.model,
            self.provider.settings.version, scope_asset_ids, top_k, candidate_limit,
        )
        if not isinstance(result, Mapping):
            raise RetrievalError("invalid_retrieval_response", "Retrieval response is invalid", retryable=True)
        return result

    def retrieve_for_job(
        self, job: Any, worker_id: str, query: str, *,
        scope_asset_ids: list[str] | None = None, top_k: int = 12,
        candidate_limit: int = 40,
    ) -> Mapping[str, Any]:
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 500:
            raise ValueError("query must contain between 2 and 500 characters")
        try:
            query_embedding = self.provider.embed([normalize_for_retrieval(query)])[0]
        except RetrievalError:
            query_embedding = None
        job_method = getattr(self.gateway, "hybrid_retrieve_for_job", None)
        if callable(job_method):
            return job_method(
                job, worker_id, query, query_embedding,
                self.provider.settings.model, self.provider.settings.version,
                scope_asset_ids, top_k, candidate_limit,
            )
        return self.gateway.hybrid_retrieve(
            job.user_id, query, query_embedding,
            self.provider.settings.model, self.provider.settings.version,
            scope_asset_ids, top_k, candidate_limit,
        )
