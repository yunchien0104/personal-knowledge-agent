import unittest
from types import SimpleNamespace

from personal_brain.retrieval_gateway import RetrievalSupabaseGateway
from personal_brain.supabase_gateway import GatewayError


class RetrievalGatewayTests(unittest.TestCase):
    def gateway(self):
        return RetrievalSupabaseGateway(
            "https://example.supabase.co", "sb_secret_" + "a" * 32
        )

    def test_backfill_is_bounded_and_validates_response(self):
        gateway = self.gateway()
        calls = []
        gateway._rpc = lambda name, arguments: calls.append((name, arguments)) or []

        self.assertEqual(gateway.retrieval_backfill_contexts("model", "v1", limit=7), [])
        self.assertEqual(calls[0][0], "get_retrieval_backfill_contexts")
        self.assertEqual(calls[0][1]["p_limit"], 7)

        gateway._rpc = lambda _name, _arguments: {"unexpected": True}
        with self.assertRaises(GatewayError):
            gateway.retrieval_backfill_contexts("model", "v1")

    def test_publication_passes_complete_owner_and_revision_identity(self):
        gateway = self.gateway()
        recorded = {}
        gateway._rpc = lambda name, arguments: recorded.update(
            {"name": name, "arguments": arguments}
        ) or 1
        context = {"userId": "user", "assetId": "asset", "revisionId": "revision"}

        result = gateway.replace_retrieval_chunks(context, "model", "v1", [{"chunkIndex": 0}])

        self.assertEqual(result, 1)
        self.assertEqual(recorded["name"], "replace_retrieval_chunks")
        self.assertEqual(recorded["arguments"]["p_user_id"], "user")
        self.assertEqual(recorded["arguments"]["p_revision_id"], "revision")

    def test_index_enqueue_preserves_pdf_rpc_and_adds_isolated_instagram_rpc(self):
        gateway = self.gateway()
        calls = []
        gateway._rpc = lambda name, arguments: calls.append((name, arguments)) or 1

        self.assertEqual(gateway.enqueue_retrieval_index_jobs("model", "v1", limit=7), 3)
        self.assertEqual(
            [name for name, _arguments in calls],
            [
                "enqueue_retrieval_index_jobs",
                "enqueue_instagram_post_retrieval_index_jobs",
                "enqueue_instagram_reel_retrieval_index_jobs",
            ],
        )

    def test_instagram_index_context_uses_only_its_namespaced_rpc(self):
        gateway = self.gateway()
        calls = []
        gateway._rpc = lambda name, arguments: calls.append((name, arguments)) or {
            "sourceType": "instagram_post"
        }
        job = SimpleNamespace(
            id="job", lease_generation=1,
            payload=SimpleNamespace(input={"sourceType": "instagram_post"}),
        )

        result = gateway.retrieval_index_context(job, "worker")

        self.assertEqual(result, {"sourceType": "instagram_post"})
        self.assertEqual(calls[0][0], "get_instagram_post_retrieval_index_context")

    def test_reel_index_context_uses_only_its_namespaced_rpc(self):
        gateway = self.gateway()
        calls = []
        gateway._rpc = lambda name, arguments: calls.append((name, arguments)) or {"sourceType": "instagram_reel"}
        job = SimpleNamespace(id="job", lease_generation=1,
            payload=SimpleNamespace(input={"sourceType": "instagram_reel"}))
        self.assertEqual(gateway.retrieval_index_context(job, "worker"), {"sourceType": "instagram_reel"})
        self.assertEqual(calls[0][0], "get_instagram_reel_retrieval_index_context")

    def test_worker_retrieval_uses_only_the_service_role_rpc(self):
        gateway = self.gateway()
        recorded = {}
        gateway._rpc = lambda name, arguments: recorded.update(
            {"name": name, "arguments": arguments}
        ) or {"results": [], "sufficiency": "unassessed"}

        gateway.hybrid_retrieve("user", "question", None, "model", "v1", ["asset"], 5, 20)

        self.assertEqual(recorded["name"], "hybrid_retrieve_for_worker")
        self.assertEqual(recorded["arguments"]["p_user_id"], "user")
        self.assertEqual(recorded["arguments"]["p_scope_asset_ids"], ["asset"])


if __name__ == "__main__":
    unittest.main()
