from __future__ import annotations

import json

from personal_brain.models import JobPayloadV1
from personal_brain.retrieval import (
    EMBEDDING_DIMENSIONS,
    EmbeddingSettings,
    RetrievalError,
    RetrievalIndexWorker,
)
from personal_brain.supabase_gateway import ClaimedJob


USER_ID = "72000000-0000-4000-8000-000000000001"
ASSET_ID = "72000000-0000-4000-8000-000000000002"
REVISION_ID = "72000000-0000-4000-8000-000000000003"
EVIDENCE_ID = "72000000-0000-4000-8000-000000000004"
JOB_ID = "72000000-0000-4000-8000-000000000005"
SOURCE_HASH = "b" * 64


def job() -> ClaimedJob:
    payload = JobPayloadV1.from_dict({
        "schemaVersion": 1,
        "jobId": JOB_ID,
        "userId": USER_ID,
        "jobType": "index_retrieval",
        "idempotencyKey": "retrieval-index:test",
        "input": {
            "assetId": ASSET_ID,
            "revisionId": REVISION_ID,
            "embeddingModel": "test-model",
            "embeddingVersion": "test-v1",
        },
    })
    return ClaimedJob(JOB_ID, USER_ID, ASSET_ID, 2, payload)


class Provider:
    settings = EmbeddingSettings(
        base_url="https://embedding.example.test/v1",
        api_key="secret",
        model="test-model",
        version="test-v1",
    )

    def __init__(self, error: RetrievalError | None = None) -> None:
        self.error = error

    def embed(self, texts):
        if self.error:
            raise self.error
        return [[1.0] * EMBEDDING_DIMENSIONS for _ in texts]


class Gateway:
    def __init__(self) -> None:
        self.claimed = False
        self.publications = []
        self.failures = []

    def enqueue_retrieval_index_jobs(self, *_args, **_kwargs):
        return 1

    def claim(self, *_args):
        if self.claimed:
            return None
        self.claimed = True
        return job()

    def heartbeat(self, *_args):
        return True

    def retrieval_index_context(self, *_args):
        text = "台\n灣科技產業"
        return {
            "userId": USER_ID,
            "assetId": ASSET_ID,
            "revisionId": REVISION_ID,
            "sourceFingerprint": SOURCE_HASH,
            "artifactObjectKey": "artifact.json",
            "evidence": [{
                "id": EVIDENCE_ID,
                "sourceHash": SOURCE_HASH,
                "excerpt": text,
                "locator": {
                    "kind": "pdf_page", "page_number": 1,
                    "start_offset": 0, "end_offset": len(text),
                    "source_hash": SOURCE_HASH,
                },
            }],
        }

    def download_artifact(self, _path):
        text = "台\n灣科技產業"
        return json.dumps({
            "schema_version": 2,
            "source_hash": SOURCE_HASH,
            "text": text,
            "retrieval_text": "台灣科技產業",
            "pages": [{
                "page_number": 1, "text": text, "retrieval_text": "台灣科技產業",
                "start_offset": 0, "end_offset": len(text), "source_hash": SOURCE_HASH,
            }],
        }).encode()

    def publish_retrieval_index(self, *_args, **kwargs):
        self.publications.append(kwargs)
        return {"chunkCount": len(kwargs["chunks"])}

    def report_failure(self, *_args, **kwargs):
        self.failures.append(kwargs)
        return "retry_wait"


def test_durable_index_worker_publishes_citation_text_and_repaired_search_text() -> None:
    gateway = Gateway()
    result = RetrievalIndexWorker(gateway, Provider(), "index-worker").process_one()
    assert result.status == "completed"
    chunk = gateway.publications[0]["chunks"][0]
    assert chunk["content"] == "台\n灣科技產業"
    assert "台灣" in chunk["searchText"]
    assert gateway.failures == []


def test_embedding_failure_is_retryable_and_never_publishes() -> None:
    gateway = Gateway()
    provider = Provider(RetrievalError("embedding_down", "down", retryable=True))
    result = RetrievalIndexWorker(gateway, provider, "index-worker").process_one()
    assert result.status == "retry_wait"
    assert gateway.publications == []
    assert gateway.failures[0]["error_code"] == "embedding_down"
