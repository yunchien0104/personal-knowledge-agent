import unittest
from unittest.mock import patch
from types import SimpleNamespace

from personal_brain.models import JobPayloadV1
from personal_brain.research_agent import ResearchAgentWorker
from personal_brain.retrieval import HybridRetriever, RetrievalError
from personal_brain.runner import build_research_worker
from personal_brain.supabase_gateway import ClaimedJob


JOB_ID = "91000000-0000-4000-8000-000000000001"
USER_ID = "92000000-0000-4000-8000-000000000002"
RUN_ID = "93000000-0000-4000-8000-000000000003"
ASSET_ID = "94000000-0000-4000-8000-000000000004"
REVISION_ID = "95000000-0000-4000-8000-000000000005"
EVIDENCE_ID = "96000000-0000-4000-8000-000000000006"
NOTE_ID = "97000000-0000-4000-8000-000000000007"
QUESTION = "What does the evidence say about revenue growth?"
EXCERPT = "Revenue increased by 20 percent in 2025, with seasonal uncertainty."


def claimed_job(*, scope_asset_ids=None) -> ClaimedJob:
    job_input = {"researchRunId": RUN_ID, "question": QUESTION}
    if scope_asset_ids is not None:
        job_input["scopeAssetIds"] = scope_asset_ids
    payload = JobPayloadV1.from_dict({
        "schemaVersion": 1,
        "jobId": JOB_ID,
        "userId": USER_ID,
        "jobType": "execute_research",
        "idempotencyKey": "research:98000000-0000-4000-8000-000000000008",
        "input": job_input,
    })
    return ClaimedJob(JOB_ID, USER_ID, None, 1, payload)


def valid_plan():
    return {
        "question": QUESTION,
        "scope": "Internal evidence only",
        "subquestions": ["revenue growth"],
        "evidenceRequirements": ["Original evidence with verbatim quotes"],
        "maxCostUsd": 0,
        "maxQueries": 1,
        "maxRounds": 1,
        "stopConditions": ["One internal retrieval round"],
    }


def valid_synthesis():
    return {
        "title": "Revenue Growth Research Draft",
        "summary": "The internal evidence reports growth and uncertainty.",
        "findings": [{
            "text": "The source reports 20 percent revenue growth in 2025.",
            "uncertainty": "This is one source and is not independently verified.",
            "citations": [{
                "evidenceId": EVIDENCE_ID,
                "quote": "Revenue increased by 20 percent in 2025",
                "relationType": "supports",
            }],
        }],
    }


class MockResearchModel:
    provider = "mock"
    model_name = "mock-research-v1"

    def __init__(self, *, plan=None, synthesis=None):
        self.plan_output = plan or valid_plan()
        self.synthesis_output = synthesis or valid_synthesis()
        self.plan_calls = 0
        self.synthesis_calls = 0

    def plan(self, _question):
        self.plan_calls += 1
        return self.plan_output

    def synthesize(self, _question, _plan, _evidence):
        self.synthesis_calls += 1
        self.evidence = _evidence
        return self.synthesis_output


class FakeRetrieval:
    def __init__(self, *, results=None, semantic_status="ready"):
        self.calls = []
        self.results = results if results is not None else [{
            "chunkId": "99000000-0000-4000-8000-000000000009",
            "evidenceId": EVIDENCE_ID,
            "assetId": ASSET_ID,
            "revisionId": REVISION_ID,
            "content": EXCERPT,
            "locator": {"kind": "pdf_page", "page_number": 2},
            "retrievalMethod": "hybrid" if semantic_status == "ready" else "lexical",
        }]
        self.semantic_status = semantic_status

    def retrieve(self, user_id, query, *, scope_asset_ids, top_k, candidate_limit):
        self.calls.append({
            "userId": user_id,
            "query": query,
            "scopeAssetIds": scope_asset_ids,
            "topK": top_k,
            "candidateLimit": candidate_limit,
        })
        return {
            "semanticStatus": self.semantic_status,
            "sufficiency": "unassessed",
            "results": self.results,
        }


class FailingEmbeddingProvider:
    settings = SimpleNamespace(model="multilingual-test-model", version="test-v1")

    def embed(self, _texts):
        raise RetrievalError("embedding_provider_unavailable", "unavailable", retryable=True)


class FakeGateway:
    def __init__(self):
        self.job = claimed_job()
        self.claimed = False
        self.heartbeat_results = []
        self.failures = []
        self.publications = []
        self.hybrid_calls = []
        self.context = {
            "researchRunId": RUN_ID,
            "question": QUESTION,
            "plan": valid_plan(),
            "evidence": [{
                "id": EVIDENCE_ID,
                "assetId": ASSET_ID,
                "revisionId": REVISION_ID,
                "excerpt": EXCERPT,
                "locator": {"kind": "pdf_page", "page_number": 2},
                "relevance": 0.75,
            }],
        }

    def claim(self, _worker_id, _lease_seconds, resource_class):
        self.resource_class = resource_class
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def heartbeat(self, _job, _worker_id, _lease_seconds):
        return self.heartbeat_results.pop(0) if self.heartbeat_results else True

    def research_context(self, _job, _worker_id, plan):
        self.plan = plan
        return self.context

    def hybrid_retrieve(self, user_id, query, query_embedding, model, version,
                        scope_asset_ids, top_k, candidate_limit):
        self.hybrid_calls.append({
            "userId": user_id,
            "query": query,
            "queryEmbedding": query_embedding,
            "scopeAssetIds": scope_asset_ids,
        })
        return {
            "semanticStatus": "degraded_embedding_unavailable",
            "sufficiency": "unassessed",
            "results": [{
                "evidenceId": EVIDENCE_ID,
                "assetId": ASSET_ID,
                "revisionId": REVISION_ID,
                "content": EXCERPT,
                "locator": {"kind": "pdf_page", "page_number": 2},
                "retrievalMethod": "lexical",
            }],
        }

    def publish_research_draft(self, _job, _worker_id, **publication):
        self.publications.append(publication)
        return {"noteId": NOTE_ID, "researchRunId": RUN_ID, "status": "draft_ready"}

    def report_failure(self, _job, _worker_id, **failure):
        self.failures.append(failure)
        return "retry_wait" if failure["retryable"] else "failed"


class ResearchAgentWorkerTests(unittest.TestCase):
    def test_runner_builds_research_with_shared_retrieval_gateway_and_provider(self):
        with patch.dict("os.environ", {
            "WORKER_ID": "research-worker",
            "SUPABASE_SERVER_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_" + "a" * 32,
            "EMBEDDING_API_BASE_URL": "https://api.example.test/v1",
            "EMBEDDING_API_KEY": "embedding-secret",
            "EMBEDDING_MODEL": "multilingual-test-model",
            "EMBEDDING_MODEL_VERSION": "test-v1",
            "EMBEDDING_DIMENSIONS": "1536",
            "LLM_API_BASE_URL": "https://llm.example.test/v1",
            "LLM_API_KEY": "llm-secret",
            "RESEARCH_MODEL": "research-test-model",
        }, clear=True):
            worker = build_research_worker()

        self.assertIs(worker.gateway, worker.retrieval.gateway)
        self.assertEqual(worker.retrieval.provider.settings.model, "multilingual-test-model")

    def test_research_claim_accepts_null_asset_and_typed_run_input(self):
        job = claimed_job()
        parsed = ClaimedJob.from_row({
            "id": JOB_ID,
            "user_id": USER_ID,
            "asset_id": None,
            "lease_generation": 1,
            "payload_json": job.payload.to_dict(),
        })
        self.assertIsNone(parsed.asset_id)
        self.assertEqual(parsed.payload.input["researchRunId"], RUN_ID)

    def test_plan_retrieve_select_synthesize_and_publish_grounded_draft(self):
        gateway = FakeGateway()
        retrieval = FakeRetrieval()
        model = MockResearchModel()
        result = ResearchAgentWorker(
            gateway, model, retrieval, "research-worker"
        ).process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.note_id, NOTE_ID)
        self.assertEqual(gateway.resource_class, "research")
        self.assertEqual(gateway.plan["maxCostUsd"], 0)
        self.assertEqual(retrieval.calls[0]["userId"], USER_ID)
        self.assertEqual(retrieval.calls[0]["query"], QUESTION)
        self.assertIsNone(retrieval.calls[0]["scopeAssetIds"])
        self.assertEqual(model.evidence[0]["id"], EVIDENCE_ID)
        self.assertEqual(model.evidence[0]["excerpt"], EXCERPT)
        self.assertEqual(model.evidence[0]["retrievalStatus"], "ready")
        self.assertEqual(len(gateway.publications), 1)
        publication = gateway.publications[0]
        self.assertIn(f"[Evidence {EVIDENCE_ID}]", publication["body"])
        self.assertEqual(publication["findings"][0]["citations"][0]["quote"],
                         "Revenue increased by 20 percent in 2025")
        self.assertEqual(gateway.failures, [])

    def test_unknown_or_non_verbatim_citation_never_publishes(self):
        synthesis = valid_synthesis()
        synthesis["findings"][0]["citations"][0]["quote"] = "Revenue doubled."
        gateway = FakeGateway()
        result = ResearchAgentWorker(
            gateway, MockResearchModel(synthesis=synthesis), FakeRetrieval(), "research-worker"
        ).process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "unsupported_research_finding")

    def test_empty_internal_retrieval_is_truthful_insufficient_evidence(self):
        gateway = FakeGateway()
        model = MockResearchModel()
        result = ResearchAgentWorker(
            gateway, model, FakeRetrieval(results=[]), "research-worker"
        ).process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(model.synthesis_calls, 0)
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "insufficient_evidence")

    def test_stale_lease_stops_before_planning(self):
        gateway = FakeGateway()
        gateway.heartbeat_results = [False]
        model = MockResearchModel()
        result = ResearchAgentWorker(
            gateway, model, FakeRetrieval(), "research-worker"
        ).process_one()

        self.assertEqual(result.status, "lease_lost")
        self.assertEqual(model.plan_calls, 0)
        self.assertEqual(gateway.publications, [])

    def test_asset_scope_is_passed_unchanged_to_database_validated_retrieval(self):
        gateway = FakeGateway()
        gateway.job = claimed_job(scope_asset_ids=[ASSET_ID])
        retrieval = FakeRetrieval()

        result = ResearchAgentWorker(
            gateway, MockResearchModel(), retrieval, "research-worker"
        ).process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(retrieval.calls[0]["scopeAssetIds"], [ASSET_ID])

    def test_lexical_fallback_is_explicit_and_preserves_provenance(self):
        gateway = FakeGateway()
        retrieval = FakeRetrieval(semantic_status="degraded_embedding_unavailable")
        model = MockResearchModel()

        result = ResearchAgentWorker(
            gateway, model, retrieval, "research-worker"
        ).process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(model.evidence[0]["retrievalMethod"], "lexical")
        self.assertEqual(
            model.evidence[0]["retrievalStatus"], "degraded_embedding_unavailable"
        )
        self.assertEqual(model.evidence[0]["locator"]["page_number"], 2)

    def test_embedding_provider_failure_uses_real_hybrid_retriever_lexical_fallback(self):
        gateway = FakeGateway()
        model = MockResearchModel()
        retrieval = HybridRetriever(gateway, FailingEmbeddingProvider())

        result = ResearchAgentWorker(
            gateway, model, retrieval, "research-worker"
        ).process_one()

        self.assertEqual(result.status, "completed")
        self.assertIsNone(gateway.hybrid_calls[0]["queryEmbedding"])
        self.assertEqual(gateway.hybrid_calls[0]["query"], QUESTION)
        self.assertEqual(model.evidence[0]["retrievalStatus"], "degraded_embedding_unavailable")

    def test_degraded_response_cannot_smuggle_semantic_results(self):
        gateway = FakeGateway()
        retrieval = FakeRetrieval(semantic_status="degraded_embedding_unavailable")
        retrieval.results[0]["retrievalMethod"] = "semantic"

        result = ResearchAgentWorker(
            gateway, MockResearchModel(), retrieval, "research-worker"
        ).process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "invalid_research_context")

    def test_invalid_or_duplicate_asset_scope_fails_before_retrieval(self):
        for scope in ([], [ASSET_ID, ASSET_ID], ["not-a-uuid"]):
            with self.subTest(scope=scope):
                gateway = FakeGateway()
                gateway.job = claimed_job(scope_asset_ids=scope)
                retrieval = FakeRetrieval()

                result = ResearchAgentWorker(
                    gateway, MockResearchModel(), retrieval, "research-worker"
                ).process_one()

                self.assertEqual(result.status, "failed")
                self.assertEqual(retrieval.calls, [])
                self.assertEqual(gateway.failures[0]["error_code"], "invalid_research_scope")


if __name__ == "__main__":
    unittest.main()
