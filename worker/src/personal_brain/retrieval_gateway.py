from __future__ import annotations

from typing import Any, Mapping

from personal_brain.supabase_gateway import GatewayError, SupabaseGateway


class RetrievalSupabaseGateway(SupabaseGateway):
    """R1-only extension that leaves the shared Worker gateway contract intact."""

    def retrieval_backfill_contexts(
        self,
        embedding_model: str,
        embedding_version: str,
        *,
        after_revision_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        result = self._rpc(
            "get_retrieval_backfill_contexts",
            {
                "p_embedding_model": embedding_model,
                "p_embedding_version": embedding_version,
                "p_after_revision_id": after_revision_id,
                "p_limit": limit,
            },
        )
        if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
            raise GatewayError(
                "invalid_retrieval_context", "Retrieval backfill response is invalid", retryable=True
            )
        return result

    def replace_retrieval_chunks(
        self,
        context: Mapping[str, Any],
        embedding_model: str,
        embedding_version: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        result = self._rpc(
            "replace_retrieval_chunks",
            {
                "p_user_id": context.get("userId"),
                "p_asset_id": context.get("assetId"),
                "p_revision_id": context.get("revisionId"),
                "p_embedding_model": embedding_model,
                "p_embedding_version": embedding_version,
                "p_chunks": chunks,
            },
        )
        if type(result) is not int or result < 0:
            raise GatewayError(
                "invalid_retrieval_publication",
                "Retrieval publication response is invalid",
                retryable=True,
            )
        return result

    def hybrid_retrieve(
        self,
        user_id: str,
        query: str,
        query_embedding: list[float] | None,
        model: str,
        version: str,
        scope_asset_ids: list[str] | None,
        top_k: int,
        candidate_limit: int,
    ) -> Mapping[str, Any]:
        result = self._rpc(
            "hybrid_retrieve_for_worker",
            {
                "p_user_id": user_id,
                "p_query": query,
                "p_query_embedding": query_embedding,
                "p_embedding_model": model,
                "p_embedding_version": version,
                "p_scope_asset_ids": scope_asset_ids,
                "p_top_k": top_k,
                "p_candidate_limit": candidate_limit,
            },
        )
        if not isinstance(result, dict):
            raise GatewayError(
                "invalid_retrieval_response", "Hybrid retrieval response is invalid", retryable=True
            )
        return result
