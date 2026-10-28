from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
import re
import unicodedata
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from personal_brain.queue_runtime import LeaseGuard, LeaseLostError, ProcessResult
from personal_brain.supabase_gateway import ClaimedJob, GatewayError


PROCESSOR_VERSION = "threads-playwright-v1"
POST_PATH = re.compile(r"^/(?:@[^/]+/post|t)/([A-Za-z0-9_-]{5,64})/?$")
ALLOWED_HOSTS = {"threads.net", "www.threads.net", "threads.com", "www.threads.com"}
BOILERPLATE_PREFIXES = ("threads, say more", "join threads", "log in")


class ThreadsFetchError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ThreadsTarget:
    canonical_url: str
    post_id: str


@dataclass(frozen=True, slots=True)
class ThreadsFetchResult:
    text: str
    retrieval_method: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ThreadsSegment:
    id: str
    text: str
    content_hash: str
    evidence_locator: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ParsedThreadsPost:
    schema_version: int
    source_type: str
    source_hash: str
    source_url: str
    post_id: str
    text: str
    segments: tuple[ThreadsSegment, ...]
    provenance: dict[str, str]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ThreadsIngestionGateway(Protocol):
    def claim(
        self, worker_id: str, lease_seconds: int, resource_class: str = "lightweight"
    ) -> ClaimedJob | None: ...
    def heartbeat(self, job: ClaimedJob, worker_id: str, lease_seconds: int) -> bool: ...
    def upload_immutable(self, object_path: str, content: bytes, content_hash: str) -> None: ...
    def publish_threads(
        self, job: ClaimedJob, worker_id: str, **result: Any
    ) -> dict[str, Any] | None: ...
    def report_failure(self, job: ClaimedJob, worker_id: str, **failure: Any) -> str | None: ...


def normalize_threads_url(value: str) -> ThreadsTarget:
    try:
        parsed = urlparse(value.strip())
    except ValueError as error:
        raise ThreadsFetchError("invalid_threads_url", "Threads URL is invalid") from error
    host = (parsed.hostname or "").lower()
    match = POST_PATH.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or host not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or match is None
    ):
        raise ThreadsFetchError(
            "invalid_threads_url", "A public Threads post URL is required"
        )
    post_id = match.group(1)
    return ThreadsTarget(f"https://www.threads.net/t/{post_id}", post_id)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.strip() for line in normalized.splitlines()).strip()


def _usable_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = _normalize_text(value)
    if not text or any(text.lower().startswith(prefix) for prefix in BOILERPLATE_PREFIXES):
        return None
    return text


def fetch_threads_post(target: ThreadsTarget) -> ThreadsFetchResult:
    """Retrieve one public post with no login or access-control bypass."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise ThreadsFetchError(
            "browser_runtime_missing", "Threads browser runtime is not installed"
        ) from error

    timeout_ms = int(os.environ.get("THREADS_FETCH_TIMEOUT_MS", "25000"))
    if not 5_000 <= timeout_ms <= 60_000:
        raise ThreadsFetchError(
            "invalid_worker_configuration", "Threads fetch timeout is invalid"
        )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="zh-TW",
            )
            def route_request(route: Any, request: Any) -> None:
                parsed = urlparse(request.url)
                if request.resource_type == "document" and (
                    parsed.scheme != "https"
                    or (parsed.hostname or "").lower() not in ALLOWED_HOSTS
                ):
                    route.abort()
                elif request.resource_type in {"font", "image", "media"}:
                    route.abort()
                else:
                    route.continue_()

            context.route("**/*", route_request)
            page = context.new_page()
            page.goto(target.canonical_url, wait_until="domcontentloaded", timeout=timeout_ms)
            final_target = normalize_threads_url(page.url)
            if final_target.post_id != target.post_id:
                raise ThreadsFetchError(
                    "unexpected_threads_redirect", "Threads redirected to a different post"
                )

            try:
                meta = _usable_text(
                    page.locator('meta[property="og:description"]').get_attribute(
                        "content", timeout=4_000
                    )
                )
            except PlaywrightTimeoutError:
                meta = None
            if meta and not meta.endswith(("…", "...")):
                return ThreadsFetchResult(meta, "open_graph")

            page.wait_for_timeout(3_000)
            values = page.locator('div[data-pressable-container="true"] span').all_inner_texts()
            lines: list[str] = []
            for value in values:
                text = _usable_text(value)
                if text and (not lines or lines[-1] != text):
                    lines.append(text)
            rendered = _usable_text("\n".join(lines))
            preview = meta.removesuffix("...").removesuffix("…").rstrip() if meta else None
            if rendered and preview and len(rendered) > len(meta) and preview in rendered:
                return ThreadsFetchResult(
                    rendered,
                    "rendered_dom",
                    ("Rendered post text was used because complete Open Graph text was unavailable.",),
                )
            if meta:
                raise ThreadsFetchError(
                    "threads_content_incomplete",
                    "Threads exposed only a truncated post preview; no partial revision was published",
                )
            raise ThreadsFetchError(
                "threads_post_inaccessible",
                "Threads did not expose public post text; paste/export fallback is required",
            )
    except ThreadsFetchError:
        raise
    except PlaywrightTimeoutError as error:
        raise ThreadsFetchError(
            "threads_fetch_timeout", "Threads timed out while loading", retryable=True
        ) from error
    except PlaywrightError as error:
        raise ThreadsFetchError(
            "threads_fetch_failed", "Threads could not be loaded", retryable=True
        ) from error


def parse_threads_post(
    target: ThreadsTarget,
    fetched: ThreadsFetchResult,
    *,
    retrieved_at: str,
    max_chunk_size: int = 150,
    overlap: int = 20,
) -> ParsedThreadsPost:
    text = _normalize_text(fetched.text)
    if not text:
        raise ThreadsFetchError("threads_post_inaccessible", "Threads post text is empty")
    if len(text) > 100_000:
        raise ThreadsFetchError("threads_post_too_large", "Threads post exceeds the text limit")
    if max_chunk_size < 1 or overlap < 0 or overlap >= max_chunk_size:
        raise ValueError("Threads chunk configuration is invalid")

    source_hash = sha256(text.encode()).hexdigest()
    segments: list[ThreadsSegment] = []
    start = 0
    while start < len(text):
        end = min(start + max_chunk_size, len(text))
        chunk = text[start:end]
        segments.append(
            ThreadsSegment(
                id=f"{start}_{end}",
                text=chunk,
                content_hash=sha256(chunk.encode()).hexdigest(),
                evidence_locator={
                    "kind": "threads_post",
                    "url": target.canonical_url,
                    "post_id": target.post_id,
                    "start_offset": start,
                    "end_offset": end,
                    "source_hash": source_hash,
                },
            )
        )
        if end == len(text):
            break
        start += max_chunk_size - overlap

    return ParsedThreadsPost(
        schema_version=1,
        source_type="threads",
        source_hash=source_hash,
        source_url=target.canonical_url,
        post_id=target.post_id,
        text=text,
        segments=tuple(segments),
        provenance={
            "retrieved_at": retrieved_at,
            "processor_version": PROCESSOR_VERSION,
            "retrieval_method": fetched.retrieval_method,
        },
        warnings=fetched.warnings,
    )


class ThreadsIngestionWorker:
    def __init__(
        self,
        gateway: ThreadsIngestionGateway,
        worker_id: str,
        *,
        lease_seconds: int = 120,
        fetcher: Callable[[ThreadsTarget], ThreadsFetchResult] = fetch_threads_post,
    ) -> None:
        self.gateway = gateway
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.fetcher = fetcher

    def _report_failure(self, job: ClaimedJob, **failure: Any) -> None:
        try:
            self.gateway.report_failure(job, self.worker_id, **failure)
        except GatewayError:
            pass

    def process_one(self) -> ProcessResult:
        job = self.gateway.claim(self.worker_id, self.lease_seconds, "media")
        if job is None:
            return ProcessResult("idle")
        try:
            if job.payload.job_type != "ingest_threads":
                raise GatewayError(
                    "invalid_claim", "Worker received an unsupported Job", retryable=False
                )
            source_url = job.payload.input.get("sourceUrl")
            external_id = job.payload.input.get("sourceExternalId")
            provided_text = job.payload.input.get("providedText")
            if not isinstance(source_url, str) or not isinstance(external_id, str):
                raise GatewayError("invalid_claim", "Threads Job input is invalid", retryable=False)
            if provided_text is not None and not isinstance(provided_text, str):
                raise GatewayError("invalid_claim", "Threads fallback text is invalid", retryable=False)
            target = normalize_threads_url(source_url)
            if target.post_id != external_id:
                raise GatewayError("invalid_claim", "Threads Job identity is invalid", retryable=False)

            with LeaseGuard(self.gateway, job, self.worker_id, self.lease_seconds) as lease:
                lease.pulse()
                if not job.created_at:
                    raise GatewayError(
                        "invalid_claim", "Threads Job creation time is missing", retryable=False
                    )
                fetched = (
                    ThreadsFetchResult(provided_text, "user_provided")
                    if provided_text is not None
                    else self.fetcher(target)
                )
                parsed = parse_threads_post(target, fetched, retrieved_at=job.created_at)
                lease.pulse()
                artifact = json.dumps(
                    parsed.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
                artifact_hash = sha256(artifact).hexdigest()
                object_path = (
                    f"{job.user_id}/{job.asset_id}/revisions/{parsed.source_hash}/"
                    f"{PROCESSOR_VERSION}/threads-post.json"
                )
                self.gateway.upload_immutable(object_path, artifact, artifact_hash)
                lease.pulse()
                lease.ensure()

            publication = self.gateway.publish_threads(
                job,
                self.worker_id,
                source_hash=parsed.source_hash,
                processor_version=PROCESSOR_VERSION,
                object_path=object_path,
                artifact_hash=artifact_hash,
                artifact_size=len(artifact),
                segments=[asdict(segment) for segment in parsed.segments],
            )
            if publication is None:
                raise LeaseLostError("Worker lease expired before publication")
            revision_id = publication.get("revisionId")
            if not isinstance(revision_id, str):
                raise GatewayError(
                    "invalid_publication_response", "Publication response is invalid", retryable=True
                )
            return ProcessResult("completed", job.id, revision_id)
        except LeaseLostError:
            return ProcessResult("lease_lost", job.id)
        except ThreadsFetchError as error:
            unsupported_codes = {
                "invalid_threads_url",
                "unexpected_threads_redirect",
                "threads_content_incomplete",
                "threads_post_inaccessible",
                "threads_post_too_large",
            }
            error_class = (
                "transient" if error.retryable
                else "unsupported" if error.code in unsupported_codes
                else "permanent"
            )
            self._report_failure(
                job,
                error_class=error_class,
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
            )
            return ProcessResult("retry_wait" if error.retryable else "failed", job.id)
        except GatewayError as error:
            self._report_failure(
                job,
                error_class="transient" if error.retryable else "permanent",
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
            )
            return ProcessResult("retry_wait" if error.retryable else "failed", job.id)
        except (OSError, TypeError, ValueError):
            self._report_failure(
                job,
                error_class="transient",
                error_code="worker_internal_error",
                error_message="Worker encountered an internal processing error",
                retryable=True,
            )
            return ProcessResult("retry_wait", job.id)
