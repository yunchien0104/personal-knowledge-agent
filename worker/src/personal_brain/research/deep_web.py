"""Authorized Web discovery/fetch contracts and provenance-safe capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import ipaddress
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4

from personal_brain.research.deep_contracts import (
    EvidenceProvenanceV1,
    EvidenceRecordV1,
    ResearchRequestV1,
)


MAX_WEB_RESULTS = 8
MAX_WEB_EXCERPT_CHARS = 12_000


class WebResearchProvider(Protocol):
    provider: str

    def discover(self, query: str, *, limit: int) -> Sequence[Mapping[str, Any]]: ...

    def fetch(self, url: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class WebCapture:
    record: EvidenceRecordV1
    canonical_url: str
    source_title: str
    source_status: str


def canonical_public_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Web Evidence URL must be a credential-free HTTPS URL")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("Web Evidence URL cannot target localhost")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Web Evidence URL cannot target a private or special IP address")
    port = parsed.port
    if port not in {None, 443}:
        raise ValueError("Web Evidence URL cannot use a non-HTTPS port")
    netloc = hostname
    path = parsed.path or "/"
    return urlunparse(("https", netloc, path, "", parsed.query, ""))


def capture_search_snippet(
    result: Mapping[str, Any], request: ResearchRequestV1, *, provider: str
) -> WebCapture:
    if request.webPolicy != "auto" or not request.permissions.webDiscovery:
        raise PermissionError("Web discovery is not authorized")
    url = canonical_public_url(str(result.get("url") or ""))
    excerpt = str(result.get("snippet") or "").strip()[:MAX_WEB_EXCERPT_CHARS]
    if not excerpt:
        raise ValueError("Web discovery result has no snippet")
    title = str(result.get("title") or url).strip()[:500]
    return WebCapture(
        EvidenceRecordV1(
            schemaVersion="evidence-record-v1", evidenceId=uuid4(), runId=request.runId,
            userId=request.userId, origin="web", sourceIdentity=url,
            sourceMetadata={"title": title, "provider": provider, "url": url},
            provenance=EvidenceProvenanceV1(captureMethod="web_discovery"),
            excerpt=excerpt, citationLocator={"url": url, "kind": "search_snippet"},
            contentHash=sha256(excerpt.encode()).hexdigest(), capturedAt=datetime.now(timezone.utc),
            storageScope="research_run", citationEligible=False, validityStatus="valid",
            isSearchSnippet=True,
        ),
        url, title, "valid",
    )


def capture_fetched_page(
    document: Mapping[str, Any], request: ResearchRequestV1, *, provider: str
) -> WebCapture:
    if request.webPolicy != "auto" or not (
        request.permissions.webDiscovery and request.permissions.webFetch
    ):
        raise PermissionError("Web fetch is not authorized")
    url = canonical_public_url(str(document.get("url") or ""))
    excerpt = str(document.get("content") or "").strip()[:MAX_WEB_EXCERPT_CHARS]
    if not excerpt:
        raise ValueError("Fetched Web source has no content")
    title = str(document.get("title") or url).strip()[:500]
    published_at = document.get("publishedAt")
    locator = document.get("locator")
    if not isinstance(locator, Mapping):
        locator = {"kind": "web_page", "url": url}
    else:
        locator = dict(locator) | {"url": url}
    return WebCapture(
        EvidenceRecordV1(
            schemaVersion="evidence-record-v1", evidenceId=uuid4(), runId=request.runId,
            userId=request.userId, origin="web", sourceIdentity=url,
            sourceMetadata={"title": title, "provider": provider, "url": url},
            provenance=EvidenceProvenanceV1(captureMethod="web_fetch"),
            excerpt=excerpt, citationLocator=dict(locator),
            contentHash=sha256(excerpt.encode()).hexdigest(), capturedAt=datetime.now(timezone.utc),
            sourcePublishedAt=published_at, storageScope="research_run",
            citationEligible=True, validityStatus="valid", isSearchSnippet=False,
        ),
        url, title, "valid",
    )


def discover_and_fetch(
    provider: WebResearchProvider, request: ResearchRequestV1, query: str
) -> list[WebCapture]:
    if request.webPolicy == "disabled":
        raise PermissionError("Web policy is disabled")
    if request.webPolicy == "suggest":
        raise PermissionError("Suggest policy requires explicit user authorization")
    results = provider.discover(query, limit=MAX_WEB_RESULTS)
    captures: list[WebCapture] = []
    seen: set[str] = set()
    for result in results[:MAX_WEB_RESULTS]:
        snippet = capture_search_snippet(result, request, provider=provider.provider)
        if snippet.canonical_url in seen:
            continue
        seen.add(snippet.canonical_url)
        document = provider.fetch(snippet.canonical_url)
        fetched = capture_fetched_page(document, request, provider=provider.provider)
        if fetched.canonical_url != snippet.canonical_url:
            raise ValueError("Fetched source identity differs from the discovered URL")
        captures.append(fetched)
    return captures
