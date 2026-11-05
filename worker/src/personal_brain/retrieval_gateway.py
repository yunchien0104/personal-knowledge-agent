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

    def enqueue_retrieval_index_jobs(
        self, embedding_model: str, embedding_version: str, *, limit: int = 25
    ) -> int:
        payload = {
                "p_embedding_model": embedding_model,
                "p_embedding_version": embedding_version,
                "p_limit": limit,
        }
        total = 0
        for function_name in (
            "enqueue_retrieval_index_jobs",
            "enqueue_instagram_post_retrieval_index_jobs",
            "enqueue_instagram_reel_retrieval_index_jobs",
        ):
            result = self._rpc(function_name, payload)
            if type(result) is not int or result < 0:
                raise GatewayError(
                    "invalid_retrieval_enqueue",
                    "Retrieval enqueue response is invalid",
                    retryable=True,
                )
            total += result
        return total

    def retrieval_index_context(self, job: Any, worker_id: str) -> Mapping[str, Any] | None:
        function_name = {
            "instagram_post": "get_instagram_post_retrieval_index_context",
            "instagram_reel": "get_instagram_reel_retrieval_index_context",
        }.get(job.payload.input.get("sourceType"), "get_retrieval_index_context")
        result = self._rpc(
            function_name,
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
            },
        )
        return result if isinstance(result, dict) else None

    def publish_retrieval_index(
        self, job: Any, worker_id: str, *, embedding_model: str,
        embedding_version: str, chunks: list[dict[str, Any]],
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "publish_retrieval_index",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_embedding_model": embedding_model,
                "p_embedding_version": embedding_version,
                "p_chunks": chunks,
            },
        )
        return result if isinstance(result, dict) else None

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

    def hybrid_retrieve_for_job(
        self, job: Any, worker_id: str, query: str,
        query_embedding: list[float] | None, model: str, version: str,
        scope_asset_ids: list[str] | None, top_k: int, candidate_limit: int,
    ) -> Mapping[str, Any]:
        result = self._rpc(
            "hybrid_retrieve_for_research_job",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
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
                "invalid_retrieval_response", "Research retrieval response is invalid", retryable=True
            )
        return result

    def record_research_retrieval(
        self,
        job: Any,
        worker_id: str,
        plan: Mapping[str, Any],
        evidence: list[Mapping[str, Any]],
        retrieval_status: str,
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "record_research_retrieval",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_plan": plan,
                "p_retrieval_status": retrieval_status,
                "p_evidence": evidence,
            },
        )
        return result if isinstance(result, dict) else None

    def record_deep_research_plan(
        self,
        job: Any,
        worker_id: str,
        *,
        plan: Mapping[str, Any],
        quality_gate: str,
        model_provider: str,
        model_name: str,
        prompt_version: str,
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "record_deep_research_plan",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_plan": plan,
                "p_quality_gate": quality_gate,
                "p_model_provider": model_provider,
                "p_model_name": model_name,
                "p_prompt_version": prompt_version,
            },
        )
        return result if isinstance(result, dict) else None

    def record_deep_research_assessment(
        self, job: Any, worker_id: str, assessment: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "record_deep_research_assessment",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_assessment": assessment,
            },
        )
        return result if isinstance(result, dict) else None

    def load_deep_research_state(
        self, job: Any, worker_id: str
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "load_deep_research_state",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
            },
        )
        return result if isinstance(result, dict) else None

    def reserve_deep_research_operation(
        self,
        job: Any,
        worker_id: str,
        *,
        operation_id: str,
        idempotency_key: str,
        action_type: str,
        cost_category: str,
        parameters_hash: str,
        estimated_cost_usd: str,
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "reserve_deep_research_operation",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_operation_id": operation_id,
                "p_idempotency_key": idempotency_key,
                "p_action_type": action_type,
                "p_cost_category": cost_category,
                "p_parameters_hash": parameters_hash,
                "p_estimated_cost_usd": estimated_cost_usd,
            },
        )
        return result if isinstance(result, dict) else None

    def ensure_deep_research_final_reserve(
        self,
        job: Any,
        worker_id: str,
        *,
        required_reserve_usd: str,
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "ensure_deep_research_final_reserve",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_required_reserve_usd": required_reserve_usd,
            },
        )
        return result if isinstance(result, dict) else None

    def start_deep_research_operation(
        self, job: Any, worker_id: str, operation_id: str
    ) -> bool:
        result = self._rpc(
            "start_deep_research_operation",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_operation_id": operation_id,
            },
        )
        return result is True

    def record_deep_research_validation_diagnostic(
        self,
        job: Any,
        worker_id: str,
        *,
        operation_id: str,
        diagnostic: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "record_deep_research_validation_diagnostic",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_operation_id": operation_id,
                "p_diagnostic": diagnostic,
            },
        )
        return result if isinstance(result, dict) else None

    def record_deep_research_evaluation_diagnostic(
        self,
        job: Any,
        worker_id: str,
        *,
        operation_id: str,
        diagnostic: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "record_deep_research_evaluation_diagnostic",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_operation_id": operation_id,
                "p_diagnostic": diagnostic,
            },
        )
        return result if isinstance(result, dict) else None

    def settle_deep_research_operation(
        self,
        job: Any,
        worker_id: str,
        *,
        operation_id: str,
        status: str,
        actual_cost_usd: str,
        result_ref: str | None = None,
        error_code: str | None = None,
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "settle_deep_research_operation",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_operation_id": operation_id,
                "p_status": status,
                "p_actual_cost_usd": actual_cost_usd,
                "p_result_ref": result_ref,
                "p_error_code": error_code,
            },
        )
        return result if isinstance(result, dict) else None

    def reserve_deep_research_finalization(
        self,
        job: Any,
        worker_id: str,
        *,
        slots: list[Mapping[str, Any]],
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "reserve_deep_research_finalization",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_slots": slots,
            },
        )
        return result if isinstance(result, dict) else None

    def record_deep_research_payload_manifest(
        self,
        job: Any,
        worker_id: str,
        *,
        stage: str,
        manifest: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "record_deep_research_payload_manifest",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_stage": stage,
                "p_manifest": manifest,
            },
        )
        return result if isinstance(result, dict) else None

    def complete_deep_research_final_operation(
        self,
        job: Any,
        worker_id: str,
        **payload: Any,
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "complete_deep_research_final_operation",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                **payload,
            },
        )
        return result if isinstance(result, dict) else None

    def load_deep_research_finalization(
        self, job: Any, worker_id: str
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "load_deep_research_finalization",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
            },
        )
        return result if isinstance(result, dict) else None

    def append_deep_research_evidence(
        self,
        job: Any,
        worker_id: str,
        *,
        operation_id: str,
        round_number: int,
        query_fingerprint: str,
        evidence: list[Mapping[str, Any]],
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "append_deep_research_evidence",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_operation_id": operation_id,
                "p_round_number": round_number,
                "p_query_fingerprint": query_fingerprint,
                "p_evidence": evidence,
            },
        )
        return result if isinstance(result, dict) else None

    def commit_deep_research_checkpoint(
        self,
        job: Any,
        worker_id: str,
        *,
        expected_checkpoint_version: int,
        state: Mapping[str, Any],
        plan_id: str | None = None,
        assessment_id: str | None = None,
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "commit_deep_research_checkpoint",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_expected_checkpoint_version": expected_checkpoint_version,
                "p_state": state,
                "p_plan_id": plan_id,
                "p_assessment_id": assessment_id,
            },
        )
        return result if isinstance(result, dict) else None

    def complete_deep_research_without_publication(
        self,
        job: Any,
        worker_id: str,
        *,
        outcome: str,
        stop_reason: str,
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "complete_deep_research_without_publication",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_outcome": outcome,
                "p_stop_reason": stop_reason,
            },
        )
        return result if isinstance(result, dict) else None

    def append_deep_web_evidence(
        self,
        job: Any,
        worker_id: str,
        *,
        operation_id: str,
        evidence: list[Mapping[str, Any]],
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "append_deep_web_evidence",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_operation_id": operation_id,
                "p_evidence": evidence,
            },
        )
        return result if isinstance(result, dict) else None

    def mark_deep_web_source_status(
        self, job: Any, worker_id: str, *, canonical_url: str, status: str
    ) -> bool:
        return self._rpc(
            "mark_deep_web_source_status",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_canonical_url": canonical_url,
                "p_status": status,
            },
        ) is True

    def record_deep_research_result(
        self,
        job: Any,
        worker_id: str,
        *,
        result_status: str,
        stop_reason: str,
        title: str | None,
        body: str | None,
        findings: list[Mapping[str, Any]],
        verification: Mapping[str, Any],
        output_hash: str | None,
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "record_deep_research_result",
            {
                "p_job_id": job.id,
                "p_worker_id": worker_id,
                "p_lease_generation": job.lease_generation,
                "p_result_status": result_status,
                "p_stop_reason": stop_reason,
                "p_title": title,
                "p_body": body,
                "p_findings": findings,
                "p_verification": verification,
                "p_output_hash": output_hash,
            },
        )
        return result if isinstance(result, dict) else None

    def publish_deep_research_draft(
        self,
        job: Any,
        worker_id: str,
        *,
        model_provider: str,
        model_name: str,
        prompt_version: str,
        output_hash: str,
        title: str,
        body: str,
        findings: list[dict[str, Any]],
    ) -> Mapping[str, Any] | None:
        result = self._rpc(
            "publish_deep_research_draft",
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
