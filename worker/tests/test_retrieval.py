import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from personal_brain.retrieval import (
    EMBEDDING_DIMENSIONS,
    EmbeddingSettings,
    HybridRetriever,
    OpenAICompatibleEmbeddingProvider,
    RetrievalError,
    RetrievalIndexer,
    chunk_normalized_pdf,
    chunk_normalized_segments,
)


SOURCE_HASH = "a" * 64
EVIDENCE_1 = "81000000-0000-4000-8000-000000000001"
EVIDENCE_2 = "81000000-0000-4000-8000-000000000002"


def normalized_fixture():
    page_one = "First paragraph explains renewable infrastructure. " * 12
    page_two = "第二頁說明人工智慧資料中心與電網需求。" * 30
    full_text = page_one + "\n\n" + page_two
    artifact = {
        "schema_version": 1,
        "source_hash": SOURCE_HASH,
        "text": full_text,
        "pages": [
            {"page_number": 1, "text": page_one, "start_offset": 0,
             "end_offset": len(page_one), "source_hash": SOURCE_HASH},
            {"page_number": 2, "text": page_two, "start_offset": len(page_one) + 2,
             "end_offset": len(full_text), "source_hash": SOURCE_HASH},
        ],
    }
    evidence = [
        {"id": EVIDENCE_1, "sourceHash": SOURCE_HASH, "excerpt": page_one,
         "locator": {"kind": "pdf_page", "page_number": 1, "start_offset": 0,
                     "end_offset": len(page_one), "source_hash": SOURCE_HASH}},
        {"id": EVIDENCE_2, "sourceHash": SOURCE_HASH, "excerpt": page_two,
         "locator": {"kind": "pdf_page", "page_number": 2,
                     "start_offset": len(page_one) + 2, "end_offset": len(full_text),
                     "source_hash": SOURCE_HASH}},
    ]
    return artifact, evidence


def settings(**changes):
    values = {
        "base_url": "https://api.example.test/v1",
        "api_key": "test-secret",
        "model": "multilingual-test-model",
        "version": "test-v1",
        "batch_size": 2,
        "timeout_seconds": 3,
        "max_retries": 1,
    }
    values.update(changes)
    return EmbeddingSettings(**values)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.body).encode()


class RecordingOpener:
    def __init__(self):
        self.calls = []

    def open(self, request, *, timeout):
        payload = json.loads(request.data)
        self.calls.append((payload, timeout))
        return FakeResponse({"model": payload["model"], "data": [
            {"index": index, "embedding": [float(index + 1)] * EMBEDDING_DIMENSIONS}
            for index, _text in enumerate(payload["input"])
        ]})


class FailingOpener:
    def __init__(self):
        self.calls = 0

    def open(self, _request, *, timeout):
        self.calls += 1
        raise URLError("offline")


class FakeGateway:
    def __init__(self, artifact=None):
        self.artifact = artifact
        self.publications = []
        self.queries = []

    def download_artifact(self, _path):
        return json.dumps(self.artifact).encode()

    def replace_retrieval_chunks(self, context, model, version, chunks):
        self.publications.append((context, model, version, chunks))
        return len(chunks)

    def hybrid_retrieve(self, user_id, query, query_embedding, model, version,
                        scope_asset_ids, top_k, candidate_limit):
        self.queries.append((user_id, query, query_embedding, scope_asset_ids))
        return {
            "semanticStatus": "ready" if query_embedding is not None
                              else "degraded_embedding_unavailable",
            "sufficiency": "unassessed",
            "results": [],
        }


class FixedProvider:
    def __init__(self, *, error=None):
        self.settings = settings()
        self.error = error

    def embed(self, texts):
        if self.error:
            raise self.error
        return [[1.0] * EMBEDDING_DIMENSIONS for _text in texts]


class RetrievalTests(unittest.TestCase):
    def test_reel_segments_preserve_caption_and_timestamp_evidence(self):
        caption, transcript = "Reel caption", "Transcript evidence"
        text = caption + "\n\n" + transcript
        artifact = {
            "schema_version": 1, "source_type": "instagram_reel",
            "source_hash": SOURCE_HASH, "text": text,
            "segments": [
                {"text": caption, "evidence_locator": {"kind": "instagram_caption",
                    "start_offset": 0, "end_offset": len(caption), "source_hash": SOURCE_HASH}},
                {"text": transcript, "evidence_locator": {"kind": "video_segment",
                    "start_seconds": 0, "end_seconds": 3.5, "start_offset": len(caption) + 2,
                    "end_offset": len(text), "source_hash": SOURCE_HASH}},
            ],
        }
        evidence = [
            {"id": EVIDENCE_1, "sourceHash": SOURCE_HASH, "excerpt": caption,
             "locator": artifact["segments"][0]["evidence_locator"]},
            {"id": EVIDENCE_2, "sourceHash": SOURCE_HASH, "excerpt": transcript,
             "locator": artifact["segments"][1]["evidence_locator"]},
        ]
        chunks = chunk_normalized_segments(artifact, evidence)
        self.assertEqual([chunk.evidence_id for chunk in chunks], [EVIDENCE_1, EVIDENCE_2])
        self.assertEqual(chunks[1].locator["end_seconds"], 3.5)

    def test_instagram_segments_map_to_exact_evidence_without_crossing_images(self):
        caption = "Caption about renewable energy. " * 12
        ocr = "Chart shows 42 percent growth. " * 12
        text = caption + "\n\n" + ocr
        artifact = {
            "schema_version": 1,
            "source_type": "instagram_post",
            "source_hash": SOURCE_HASH,
            "text": text,
            "segments": [
                {
                    "text": caption,
                    "evidence_locator": {
                        "kind": "instagram_post_caption", "start_offset": 0,
                        "end_offset": len(caption), "source_hash": SOURCE_HASH,
                    },
                },
                {
                    "text": ocr,
                    "evidence_locator": {
                        "kind": "instagram_image", "image_index": 1,
                        "start_offset": len(caption) + 2, "end_offset": len(text),
                        "source_hash": SOURCE_HASH,
                    },
                },
            ],
        }
        evidence = [
            {"id": EVIDENCE_1, "sourceHash": SOURCE_HASH, "excerpt": caption,
             "locator": artifact["segments"][0]["evidence_locator"]},
            {"id": EVIDENCE_2, "sourceHash": SOURCE_HASH, "excerpt": ocr,
             "locator": artifact["segments"][1]["evidence_locator"]},
        ]

        chunks = chunk_normalized_segments(
            artifact, evidence, max_characters=240, overlap_characters=40
        )

        self.assertGreater(len(chunks), 2)
        self.assertEqual({chunk.evidence_id for chunk in chunks}, {EVIDENCE_1, EVIDENCE_2})
        for chunk in chunks:
            segment = artifact["segments"][0 if chunk.evidence_id == EVIDENCE_1 else 1]
            local_start = chunk.start_offset - segment["evidence_locator"]["start_offset"]
            local_end = chunk.end_offset - segment["evidence_locator"]["start_offset"]
            self.assertEqual(chunk.content, segment["text"][local_start:local_end])

    def test_deterministic_chunks_use_full_artifact_and_never_cross_evidence(self):
        artifact, evidence = normalized_fixture()
        first = chunk_normalized_pdf(artifact, evidence, max_characters=240, overlap_characters=40)
        second = chunk_normalized_pdf(artifact, evidence, max_characters=240, overlap_characters=40)

        self.assertEqual(first, second)
        self.assertGreater(len(first), 2)
        self.assertEqual([chunk.chunk_index for chunk in first], list(range(len(first))))
        for chunk in first:
            page = artifact["pages"][0 if chunk.evidence_id == EVIDENCE_1 else 1]
            local_start = chunk.start_offset - page["start_offset"]
            local_end = chunk.end_offset - page["start_offset"]
            self.assertEqual(chunk.content, page["text"][local_start:local_end])
            self.assertLessEqual(len(chunk.content), 240)

    def test_evidence_mapping_requires_fingerprint_page_and_offsets(self):
        artifact, evidence = normalized_fixture()
        evidence[0]["locator"]["page_number"] = 99
        with self.assertRaisesRegex(RetrievalError, "exactly one"):
            chunk_normalized_pdf(artifact, evidence)

    def test_full_document_text_must_match_pages(self):
        artifact, evidence = normalized_fixture()
        artifact["text"] = "short Evidence excerpt"
        with self.assertRaisesRegex(RetrievalError, "inconsistent"):
            chunk_normalized_pdf(artifact, evidence)

    def test_adapter_batches_validates_and_caches_by_hash_model_version(self):
        provider = OpenAICompatibleEmbeddingProvider(settings())
        opener = RecordingOpener()
        provider._opener = opener

        vectors = provider.embed(["one", "two", "one", "three"])
        cached = provider.embed(["one", "three"])

        self.assertEqual(len(vectors), 4)
        self.assertEqual(vectors[0], vectors[2])
        self.assertEqual(len(cached), 2)
        self.assertEqual([len(call[0]["input"]) for call in opener.calls], [2, 1])
        self.assertTrue(all(call[1] == 3 for call in opener.calls))

    def test_adapter_retries_bounded_then_reports_provider_failure(self):
        provider = OpenAICompatibleEmbeddingProvider(settings(max_retries=1))
        opener = FailingOpener()
        provider._opener = opener
        with patch("personal_brain.retrieval.time.sleep"):
            with self.assertRaises(RetrievalError) as raised:
                provider.embed(["query"])
        self.assertEqual(raised.exception.code, "embedding_provider_unavailable")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(opener.calls, 2)

    def test_invalid_dimension_and_empty_input_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "1536"):
            OpenAICompatibleEmbeddingProvider(settings(dimensions=3))
        provider = OpenAICompatibleEmbeddingProvider(settings())
        with self.assertRaises(ValueError):
            provider.embed([])

    def test_embedding_failure_never_publishes_or_mutates_source(self):
        artifact, evidence = normalized_fixture()
        gateway = FakeGateway(artifact)
        provider = FixedProvider(error=RetrievalError("provider_down", "down", retryable=True))
        indexer = RetrievalIndexer(gateway, provider)
        context = {
            "artifactObjectKey": "owner/asset/revisions/hash/native-text.json",
            "sourceFingerprint": SOURCE_HASH,
            "evidence": evidence,
            "userId": "owner", "assetId": "asset", "revisionId": "revision",
        }
        with self.assertRaises(RetrievalError):
            indexer.index_context(context)
        self.assertEqual(gateway.publications, [])

    def test_index_publication_is_stable_and_idempotent_input(self):
        artifact, evidence = normalized_fixture()
        gateway = FakeGateway(artifact)
        indexer = RetrievalIndexer(gateway, FixedProvider())
        context = {
            "artifactObjectKey": "owner/asset/revisions/hash/native-text.json",
            "sourceFingerprint": SOURCE_HASH,
            "evidence": evidence,
            "userId": "owner", "assetId": "asset", "revisionId": "revision",
        }
        first_count = indexer.index_context(context)
        second_count = indexer.index_context(context)
        self.assertEqual(first_count, second_count)
        self.assertEqual(gateway.publications[0][3], gateway.publications[1][3])

    def test_chinese_query_uses_semantic_vector_and_keeps_scope(self):
        gateway = FakeGateway()
        result = HybridRetriever(gateway, FixedProvider()).retrieve(
            "owner", "請摘要 AI 資料中心對電網的影響", scope_asset_ids=["pdf-a"]
        )
        self.assertIsNotNone(gateway.queries[0][2])
        self.assertEqual(gateway.queries[0][3], ["pdf-a"])
        self.assertEqual(result["sufficiency"], "unassessed")

    def test_embedding_failure_degrades_explicitly_to_lexical_only(self):
        gateway = FakeGateway()
        provider = FixedProvider(error=RetrievalError("timeout", "timeout", retryable=True))
        result = HybridRetriever(gateway, provider).retrieve("owner", "renewable infrastructure")
        self.assertIsNone(gateway.queries[0][2])
        self.assertEqual(result["semanticStatus"], "degraded_embedding_unavailable")
        self.assertEqual(result["sufficiency"], "unassessed")

    def test_query_limits_and_empty_explicit_scope_fail_before_provider_call(self):
        retriever = HybridRetriever(FakeGateway(), FixedProvider())
        with self.assertRaises(ValueError):
            retriever.retrieve("owner", "x" * 501)
        with self.assertRaises(ValueError):
            retriever.retrieve("owner", "valid query", scope_asset_ids=[])
        with self.assertRaises(ValueError):
            retriever.retrieve("owner", "valid query", top_k=21, candidate_limit=20)


if __name__ == "__main__":
    unittest.main()
