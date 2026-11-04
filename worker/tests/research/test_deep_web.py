from __future__ import annotations

from uuid import uuid4

import pytest

from personal_brain.research.deep_contracts import ResearchRequestV1
from personal_brain.research.deep_web import (
    canonical_public_url,
    capture_search_snippet,
    discover_and_fetch,
)


def request(policy: str = "auto") -> ResearchRequestV1:
    auto = policy == "auto"
    return ResearchRequestV1.model_validate({
        "schemaVersion": "research-request-v1", "requestId": str(uuid4()),
        "runId": str(uuid4()), "idempotencyKey": str(uuid4()), "userId": str(uuid4()),
        "objective": "Verify a current Web claim", "originalQuestion": "What does the source say?",
        "executionMode": "deep", "internalKnowledgeScope": {"selectedAssetIds": None},
        "webPolicy": policy,
        "permissions": {"internalKnowledge": True, "webDiscovery": policy != "disabled", "webFetch": auto},
        "effectiveLimits": {"maxRounds": 3, "maxQueries": 4, "maxCostUsd": "1.0", "maxDurationSeconds": 300},
        "analysisSpec": None,
    })


class FakeWebProvider:
    provider = "mock-web"

    def __init__(self) -> None:
        self.fetches: list[str] = []

    def discover(self, query: str, *, limit: int):
        assert query == "current claim"
        assert limit == 8
        return [{"url": "https://example.com/report#section", "title": "Report", "snippet": "Search-only text"}]

    def fetch(self, url: str):
        self.fetches.append(url)
        return {"url": url, "title": "Report", "content": "Primary source body",
                "locator": {"kind": "web_page", "section": "results"}}


def test_auto_policy_fetches_citation_eligible_run_scoped_evidence() -> None:
    provider = FakeWebProvider()
    captures = discover_and_fetch(provider, request("auto"), "current claim")

    assert provider.fetches == ["https://example.com/report"]
    assert len(captures) == 1
    assert captures[0].record.origin == "web"
    assert captures[0].record.storageScope == "research_run"
    assert captures[0].record.citationEligible is True
    assert captures[0].record.isSearchSnippet is False
    assert captures[0].record.citationLocator["url"] == "https://example.com/report"


def test_disabled_and_suggest_policies_block_execution() -> None:
    provider = FakeWebProvider()
    for policy in ("disabled", "suggest"):
        with pytest.raises(PermissionError):
            discover_and_fetch(provider, request(policy), "current claim")
    assert provider.fetches == []


def test_search_snippet_is_never_citation_eligible() -> None:
    capture = capture_search_snippet(
        {"url": "https://example.com/report", "title": "Report", "snippet": "snippet"},
        request("auto"), provider="mock-web",
    )
    assert capture.record.isSearchSnippet is True
    assert capture.record.citationEligible is False


@pytest.mark.parametrize("url", [
    "http://example.com/report", "https://localhost/report", "https://127.0.0.1/report",
    "https://10.0.0.1/report", "https://user:secret@example.com/report",
])
def test_private_or_credentialed_web_targets_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        canonical_public_url(url)
