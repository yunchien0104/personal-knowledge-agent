"""Durable, lease-fenced execution for Deep Research R2B/R2C/R2D."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Any, Callable, Mapping
from uuid import UUID, NAMESPACE_URL, uuid5

from personal_brain.llm_client import LLMUsage
from personal_brain.queue_runtime import LeaseGuard, LeaseLostError, ProcessResult
from personal_brain.research.deep_contracts import (
    EvidenceAssessmentV1,
    EvidenceProvenanceV1,
    EvidenceRecordV1,
    ResearchActionV1,
    ResearchBudgetStateV1,
    ResearchIterationStateV1,
    ResearchPlanV1,
    ResearchRequestV1,
)
from personal_brain.research.deep_controller import (
    ResearchIterationController,
    material_coverage_gain,
    query_fingerprint,
)
from personal_brain.research.deep_evaluation import EvidenceEvaluator
from personal_brain.research.deep_finalization import (
    FinalizationContractValidationError,
    DeepFinalizationEngine,
    FinalizationPayloadLimitError,
    FinalizationProviderMetadataError,
    FinalizationSlot,
)
from personal_brain.research.deep_web import WebResearchProvider, discover_and_fetch
from personal_brain.research.deep_planning import (
    DeepResearchPlanner,
    build_planning_context_pack,
)
from personal_brain.research.publication_adapter import adapt_r2_publication
from personal_brain.research_agent import _validate_retrieval_result
from personal_brain.supabase_gateway import GatewayError


ZERO = Decimal("0")
RECOVERABLE_OPERATION_STATUSES = frozenset({"reserved"})
REAUTHORIZATION_REQUIRED_STATUSES = frozenset({
    "failed", "cancelled", "abandoned", "outcome_unknown",
})
RUNNING_RECONCILIATION_STATUS = "running_reconciliation_required"
ADMITTED_OPERATION_STATUS = "admitted"


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


def _finalization_plan(
    request: ResearchRequestV1, plan: ResearchPlanV1,
) -> dict[str, Any]:
    value = plan.model_dump(mode="json")
    if request.analysisSpec is not None:
        value["analysisSpec"] = request.analysisSpec.model_dump(mode="json")
    return value


def _operation_id(run_id: UUID, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"personal-brain:{run_id}:{key}")


def _provider_result_ref(
    request_ids: tuple[str, ...], usage: Any, finish_reasons: tuple[str, ...]
) -> str | None:
    if not request_ids and not finish_reasons and not getattr(usage, "total_tokens", 0):
        return None
    value = json.dumps({
        "requestIds": list(request_ids),
        "finishReasons": list(finish_reasons),
        "usage": {
            "inputTokens": int(getattr(usage, "input_tokens", 0)),
            "outputTokens": int(getattr(usage, "output_tokens", 0)),
            "totalTokens": int(getattr(usage, "total_tokens", 0)),
            "cachedInputTokens": int(getattr(usage, "cached_input_tokens", 0)),
            "cacheWriteInputTokens": int(getattr(usage, "cache_write_input_tokens", 0)),
            "reasoningTokens": int(getattr(usage, "reasoning_tokens", 0)),
        },
    }, separators=(",", ":"))
    if len(value) > 1000:
        raise ValueError("provider operation metadata exceeds the durable result_ref limit")
    return value


def _budget(raw: Any, limit: Decimal) -> ResearchBudgetStateV1:
    value = raw if isinstance(raw, Mapping) else {}
    return ResearchBudgetStateV1.model_validate({
        "limitUsd": value.get("limitUsd", limit),
        "protectedFinalReportReserveUsd": value.get(
            "protectedFinalReportReserveUsd", min(Decimal("0.1"), limit)
        ),
        "reservedUsd": value.get("reservedUsd", ZERO),
        "actualUsd": value.get("actualUsd", ZERO),
        "uncertainUsd": value.get("uncertainUsd", ZERO),
    })


def _evidence_records(
    rows: list[Mapping[str, Any]], request: ResearchRequestV1
) -> list[EvidenceRecordV1]:
    records: list[EvidenceRecordV1] = []
    seen: set[UUID] = set()
    now = datetime.now(timezone.utc)
    for row in rows:
        if row.get("origin") == "web":
            try:
                records.append(EvidenceRecordV1.model_validate(row))
            except ValueError:
                continue
            continue
        try:
            evidence_id = UUID(str(row.get("evidenceId") or row.get("id")))
            asset_id = UUID(str(row.get("assetId")))
            revision_id = UUID(str(row.get("revisionId")))
        except (TypeError, ValueError):
            continue
        excerpt = str(row.get("content") or row.get("excerpt") or "").strip()
        locator = row.get("locator")
        if evidence_id in seen or not excerpt or not isinstance(locator, Mapping):
            continue
        seen.add(evidence_id)
        source_hash = str(row.get("sourceHash") or "")
        if re.fullmatch(r"[0-9a-f]{64}", source_hash) is None:
            source_hash = sha256(excerpt.encode()).hexdigest()
        records.append(EvidenceRecordV1(
            schemaVersion="evidence-record-v1",
            evidenceId=evidence_id,
            runId=request.runId,
            userId=request.userId,
            origin="internal",
            sourceIdentity=f"asset:{asset_id}:revision:{revision_id}",
            sourceMetadata={
                "assetId": str(asset_id), "revisionId": str(revision_id),
                "retrievalMethod": row.get("retrievalMethod", "lexical"),
                "retrievalScore": row.get("retrievalScore", row.get("score", 0)),
            },
            provenance=EvidenceProvenanceV1(
                assetId=asset_id, revisionId=revision_id, captureMethod="hybrid_retrieval"
            ),
            excerpt=excerpt,
            citationLocator=dict(locator),
            contentHash=source_hash,
            capturedAt=now,
            storageScope="knowledge_base",
            citationEligible=True,
            validityStatus="valid",
        ))
    return records


def _publication_rows(records: list[EvidenceRecordV1]) -> list[dict[str, Any]]:
    return [{
        "id": str(item.evidenceId),
        "origin": item.origin,
        "assetId": str(item.provenance.assetId) if item.provenance.assetId else None,
        "revisionId": str(item.provenance.revisionId) if item.provenance.revisionId else None,
        "excerpt": item.excerpt,
        "locator": item.citationLocator,
    } for item in records if item.citationEligible and item.validityStatus == "valid"]


def _next_planned_retrieval_action(
    request: ResearchRequestV1,
    plan: ResearchPlanV1,
    used_fingerprints: list[str],
    *,
    round_number: int,
) -> ResearchActionV1 | None:
    """Return the next authorized unused R2B proposal for the current round."""
    used = set(used_fingerprints)
    for proposal in plan.retrievalProposals:
        fingerprint = query_fingerprint(proposal.query)
        if fingerprint in used:
            continue
        if proposal.source == "internal":
            action_type = "search_internal"
        elif request.webPolicy == "auto" and request.permissions.webDiscovery:
            action_type = "discover_web"
        else:
            continue
        return ResearchActionV1(
            actionId=_operation_id(
                request.runId,
                f"action:{round_number}:{fingerprint}",
            ),
            actionType=action_type,
            reason="Execute the next authorized R2B retrieval proposal",
            query=proposal.query,
            gapIds=[],
        )
    return None


class DeepResearchCoordinator:
    """Runs one claimed Deep Research job without creating a second queue."""

    def __init__(
        self,
        gateway: Any,
        retrieval: Any,
        *,
        planner: DeepResearchPlanner | None = None,
        evaluator: EvidenceEvaluator | None = None,
        controller: ResearchIterationController | None = None,
        orchestrator: Any | None = None,
        finalizer: DeepFinalizationEngine | None = None,
        web_provider: WebResearchProvider | None = None,
        web_operation_estimate_usd: Decimal = Decimal("0.0100"),
        require_provider_success: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.gateway = gateway
        self.retrieval = retrieval
        self.planner = planner or DeepResearchPlanner()
        self.evaluator = evaluator or EvidenceEvaluator()
        self.controller = controller or ResearchIterationController()
        self.orchestrator = orchestrator
        self.finalizer = finalizer
        self.web_provider = web_provider
        self.web_operation_estimate_usd = web_operation_estimate_usd
        self.require_provider_success = require_provider_success
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _deadline_exhausted(self, bundle: Mapping[str, Any]) -> bool:
        raw = bundle.get("deadlineAt")
        if not isinstance(raw, str):
            raise GatewayError(
                "missing_research_deadline",
                "Deep Research deadline is unavailable",
                retryable=False,
            )
        deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            raise GatewayError(
                "invalid_research_deadline",
                "Deep Research deadline is invalid",
                retryable=False,
            )
        return self.clock() >= deadline

    def _terminal_state(
        self,
        state: ResearchIterationStateV1,
        *,
        reason: str,
        outcome: str = "qualified_partial",
    ) -> ResearchIterationStateV1:
        return state.model_copy(update={
            "checkpointVersion": state.checkpointVersion + 1,
            "executionPhase": "terminal",
            "stopReason": reason,
            "outcome": outcome,
            "proposedActions": [],
            "approvedActionIds": [],
        })

    def _load(self, job: Any, worker_id: str) -> Mapping[str, Any]:
        value = self.gateway.load_deep_research_state(job, worker_id)
        if value is None:
            raise LeaseLostError("Worker lease is no longer valid")
        return value

    def _reserve_start(
        self, job: Any, worker_id: str, request: ResearchRequestV1,
        key: str, action: str, category: str, parameters: Any,
        estimated: Decimal = ZERO,
    ) -> tuple[UUID, str]:
        operation_id = _operation_id(request.runId, key)
        result = self.gateway.reserve_deep_research_operation(
            job, worker_id, operation_id=str(operation_id), idempotency_key=key,
            action_type=action, cost_category=category,
            parameters_hash=_canonical_hash(parameters), estimated_cost_usd=str(estimated),
        )
        if result is None:
            raise LeaseLostError("Worker lease is no longer valid")
        status = result.get("status")
        if status == "budget_exhausted":
            return operation_id, status
        if status in {"completed", *REAUTHORIZATION_REQUIRED_STATUSES}:
            return operation_id, str(status)
        if status == "running":
            # A previous worker may already have crossed the external boundary.
            # The durable output/settlement must be reconciled before a new paid call.
            return operation_id, RUNNING_RECONCILIATION_STATUS
        if status not in RECOVERABLE_OPERATION_STATUSES:
            raise GatewayError("invalid_operation_state", "Research operation state is invalid", retryable=False)
        if not self.gateway.start_deep_research_operation(
            job, worker_id, str(operation_id)
        ):
            raise LeaseLostError("Worker lease is no longer valid")
        return operation_id, ADMITTED_OPERATION_STATUS

    def _settle(
        self, job: Any, worker_id: str, operation_id: UUID, *,
        status: str = "completed", actual_cost_usd: Decimal = ZERO,
        error_code: str | None = None, result_ref: str | None = None,
    ) -> None:
        if self.gateway.settle_deep_research_operation(
            job, worker_id, operation_id=str(operation_id), status=status,
            actual_cost_usd=format(actual_cost_usd, "f"), error_code=error_code,
            result_ref=result_ref,
        ) is None:
            raise LeaseLostError("Worker lease is no longer valid")

    def _checkpoint(
        self, job: Any, worker_id: str, state: ResearchIterationStateV1,
        *, plan_id: UUID | None = None, assessment_id: UUID | None = None,
    ) -> None:
        result = self.gateway.commit_deep_research_checkpoint(
            job, worker_id, expected_checkpoint_version=state.checkpointVersion - 1,
            state=state.model_dump(mode="json"),
            plan_id=str(plan_id) if plan_id else None,
            assessment_id=str(assessment_id) if assessment_id else None,
        )
        if result is None:
            raise LeaseLostError("Worker lease is no longer valid")

    def _begin_finalization(
        self,
        state: ResearchIterationStateV1,
        *,
        reason: str,
        outcome: str,
    ) -> ResearchIterationStateV1:
        return state.model_copy(update={
            "checkpointVersion": state.checkpointVersion + 1,
            "executionPhase": "synthesizing",
            "retrievalStopReason": reason,
            "targetOutcome": outcome,
            "stopReason": None,
            "outcome": None,
            "proposedActions": [],
            "approvedActionIds": [],
        })

    def _ensure_finalization_reserve(
        self,
        job: Any,
        worker_id: str,
    ) -> Mapping[str, Any] | None:
        if self.finalizer is None:
            return None
        required = self.finalizer.maximum_cost_usd()
        result = self.gateway.ensure_deep_research_final_reserve(
            job,
            worker_id,
            required_reserve_usd=format(required, "f"),
        )
        if result is None:
            raise LeaseLostError(
                "Worker lease expired while protecting finalization budget"
            )
        if result.get("status") == "budget_exhausted":
            raise GatewayError(
                "deep_finalization_budget_infeasible",
                "The run cannot preserve its worst-case finalization budget",
                retryable=False,
            )
        if result.get("status") != "protected":
            raise GatewayError(
                "invalid_finalization_budget_state",
                "The protected finalization budget response is invalid",
                retryable=False,
            )
        return result

    def _final_slot_payload(
        self,
        request: ResearchRequestV1,
        slot: FinalizationSlot,
    ) -> dict[str, Any]:
        return {
            "operationId": str(_operation_id(request.runId, slot.key)),
            "idempotencyKey": slot.key,
            "actionType": slot.action_type,
            "costCategory": slot.cost_category,
            "parametersHash": _canonical_hash(slot.parameters),
            "estimatedCostUsd": format(slot.estimated_cost_usd, "f"),
        }

    def _reserve_finalization(
        self,
        job: Any,
        worker_id: str,
        request: ResearchRequestV1,
        slots: tuple[FinalizationSlot, ...],
    ) -> dict[str, str]:
        result = self.gateway.reserve_deep_research_finalization(
            job,
            worker_id,
            slots=[self._final_slot_payload(request, slot) for slot in slots],
        )
        if result is None:
            raise LeaseLostError("Worker lease expired before finalization reservation")
        operations = result.get("operations")
        if not isinstance(operations, list):
            raise GatewayError(
                "invalid_finalization_reservation",
                "Deep finalization reservation response is invalid",
                retryable=False,
            )
        statuses = {
            str(item.get("idempotencyKey")): str(item.get("status"))
            for item in operations if isinstance(item, Mapping)
        }
        if set(statuses) != {slot.key for slot in slots}:
            raise GatewayError(
                "invalid_finalization_reservation",
                "Deep finalization reservations are incomplete",
                retryable=False,
            )
        return statuses

    def _load_finalization_artifacts(
        self, job: Any, worker_id: str
    ) -> dict[str, Mapping[str, Any]]:
        result = self.gateway.load_deep_research_finalization(job, worker_id)
        if result is None:
            raise LeaseLostError("Worker lease expired while loading finalization")
        artifacts = result.get("artifacts")
        if not isinstance(artifacts, list):
            raise GatewayError(
                "invalid_finalization_state",
                "Deep finalization artifacts are invalid",
                retryable=False,
            )
        return {
            str(item.get("operationId")): item
            for item in artifacts if isinstance(item, Mapping)
        }

    def _cancel_final_slots(
        self,
        job: Any,
        worker_id: str,
        request: ResearchRequestV1,
        slots: tuple[FinalizationSlot, ...],
        statuses: dict[str, str],
    ) -> None:
        for slot in slots:
            if statuses.get(slot.key) != "reserved":
                continue
            self._settle(
                job,
                worker_id,
                _operation_id(request.runId, slot.key),
                status="cancelled",
                error_code="finalization_slot_unused",
            )
            statuses[slot.key] = "cancelled"

    def _settle_final_call_error(
        self,
        job: Any,
        worker_id: str,
        operation_id: UUID,
        error: Exception,
    ) -> None:
        from personal_brain.llm_client import StructuredLLMError
        from personal_brain.research.llm import ResearchModelValidationError
        from personal_brain.research.verifier import ClaimVerificationValidationError

        call = getattr(error, "call", None)
        if isinstance(error, StructuredLLMError):
            usage = error.usage
            cost = self.finalizer.pricing.cost(usage) if (
                self.finalizer is not None
                and usage.input_tokens > 0 and usage.output_tokens > 0
                and usage.cached_input_tokens == 0
                and usage.cache_write_input_tokens == 0
            ) else ZERO
            self._settle(
                job,
                worker_id,
                operation_id,
                status="outcome_unknown" if error.outcome_unknown else "failed",
                actual_cost_usd=cost,
                error_code=f"final_{error.error_kind}"[:128],
                result_ref=_provider_result_ref(
                    (error.request_id,) if error.request_id else (),
                    usage,
                    (error.finish_reason,) if error.finish_reason else (),
                ),
            )
            return
        if isinstance(error, FinalizationProviderMetadataError):
            self._settle(
                job,
                worker_id,
                operation_id,
                status="outcome_unknown",
                error_code=f"final_{error.error_kind}"[:128],
                result_ref=_provider_result_ref(
                    (error.call.request_id,) if error.call.request_id else (),
                    error.call.usage,
                    (error.call.finish_reason,) if error.call.finish_reason else (),
                ),
            )
            return
        if isinstance(error, (
            ResearchModelValidationError,
            ClaimVerificationValidationError,
            FinalizationContractValidationError,
        )):
            assert self.finalizer is not None and call is not None
            try:
                cost = self.finalizer.validate_transport(call)
            except FinalizationProviderMetadataError as metadata_error:
                self._settle_final_call_error(
                    job, worker_id, operation_id, metadata_error
                )
                return
            self._settle(
                job,
                worker_id,
                operation_id,
                status="failed",
                actual_cost_usd=cost,
                error_code="final_contract_invalid",
                result_ref=_provider_result_ref(
                    (call.request_id,) if call.request_id else (),
                    call.usage,
                    (call.finish_reason,) if call.finish_reason else (),
                ),
            )
            return
        self._settle(
            job,
            worker_id,
            operation_id,
            status="failed",
            error_code="finalization_failed_before_provider",
        )

    def _execute_final_slot(
        self,
        job: Any,
        worker_id: str,
        request: ResearchRequestV1,
        slot: FinalizationSlot,
        status: str,
        artifacts: dict[str, Mapping[str, Any]],
        artifact_key: str,
        invoke: Callable[[], tuple[Mapping[str, Any], Any | None]],
    ) -> Mapping[str, Any]:
        operation_id = _operation_id(request.runId, slot.key)
        operation_key = str(operation_id)
        if status == "completed":
            durable = artifacts.get(operation_key)
            if durable is None or durable.get("artifactType") != slot.artifact_type:
                raise GatewayError(
                    "missing_finalization_artifact",
                    "Completed finalization operation has no durable artifact",
                    retryable=False,
                )
            artifact = durable.get("artifact")
            if not isinstance(artifact, Mapping):
                raise GatewayError(
                    "invalid_finalization_artifact",
                    "Durable finalization artifact is invalid",
                    retryable=False,
                )
            return artifact
        if status != "reserved":
            raise GatewayError(
                "deep_finalization_reconciliation_required",
                "A finalization operation requires reconciliation or new authorization",
                retryable=False,
            )
        if not self.gateway.start_deep_research_operation(
            job, worker_id, operation_key
        ):
            raise LeaseLostError("Worker lease expired before finalization call")
        try:
            artifact, call = invoke()
            if self.finalizer is None:
                raise ValueError("Deep finalization is unavailable")
            if call is None:
                actual_cost = ZERO
                provider = "deterministic"
                model_name = "none"
                request_id = None
                finish_reason = "deterministic"
                usage = LLMUsage()
                latency_ms = 0
            else:
                actual_cost = self.finalizer.validate_transport(call)
                provider = call.provider
                model_name = call.model
                request_id = call.request_id
                finish_reason = call.finish_reason
                usage = call.usage
                latency_ms = call.latency_ms
            artifact_dict = dict(artifact)
            output_hash = _canonical_hash(artifact_dict)
            completed = self.gateway.complete_deep_research_final_operation(
                job,
                worker_id,
                p_operation_id=operation_key,
                p_artifact_type=slot.artifact_type,
                p_artifact_key=artifact_key,
                p_artifact=artifact_dict,
                p_output_hash=output_hash,
                p_provider=provider,
                p_model_name=model_name,
                p_request_id=request_id,
                p_finish_reason=finish_reason,
                p_input_tokens=usage.input_tokens,
                p_output_tokens=usage.output_tokens,
                p_reasoning_tokens=usage.reasoning_tokens,
                p_actual_cost_usd=format(actual_cost, "f"),
                p_latency_ms=latency_ms,
                p_result_ref=_provider_result_ref(
                    (request_id,) if request_id else (),
                    usage,
                    (finish_reason,) if finish_reason else (),
                ),
            )
            if completed is None:
                raise LeaseLostError(
                    "Worker lease expired before finalization artifact commit"
                )
            return artifact_dict
        except LeaseLostError:
            raise
        except Exception as error:
            self._settle_final_call_error(job, worker_id, operation_id, error)
            from personal_brain.llm_client import StructuredLLMError
            from personal_brain.research.llm import ResearchModelValidationError
            from personal_brain.research.verifier import (
                ClaimVerificationValidationError,
            )

            if isinstance(error, (
                ResearchModelValidationError,
                ClaimVerificationValidationError,
                FinalizationContractValidationError,
            )):
                raise GatewayError(
                    "deep_finalization_contract_invalid",
                    "A final-stage provider result failed its Backend contract",
                    retryable=False,
                ) from error
            if isinstance(error, FinalizationProviderMetadataError):
                raise GatewayError(
                    "deep_finalization_reconciliation_required",
                    "Final-stage provider metadata is insufficient for safe accounting",
                    retryable=False,
                ) from error
            if isinstance(error, StructuredLLMError):
                raise GatewayError(
                    (
                        "deep_finalization_provider_outcome_unknown"
                        if error.outcome_unknown
                        else "deep_finalization_provider_error"
                    ),
                    "A final-stage provider call did not produce an accepted result",
                    retryable=False,
                ) from error
            if isinstance(error, FinalizationPayloadLimitError):
                raise GatewayError(
                    "deep_finalization_payload_limit",
                    "A final-stage payload exceeds its configured safe limit",
                    retryable=False,
                ) from error
            raise

    def _run_finalization(
        self,
        job: Any,
        worker_id: str,
        request: ResearchRequestV1,
        plan: ResearchPlanV1,
        records: list[EvidenceRecordV1],
        state: ResearchIterationStateV1,
        assessment: EvidenceAssessmentV1 | None,
    ) -> tuple[Any, ResearchIterationStateV1]:
        from personal_brain.research.contracts import VerificationResult

        if self.finalizer is None:
            raise GatewayError(
                "deep_finalization_unavailable",
                "Deep finalization is not configured",
                retryable=False,
            )
        if state.executionPhase not in {
            "synthesizing", "verifying", "repairing", "publishing"
        }:
            raise GatewayError(
                "invalid_finalization_state",
                "Deep finalization checkpoint is invalid",
                retryable=False,
            )
        publication_rows = _publication_rows(records)
        finalization_plan = _finalization_plan(request, plan)
        preflight = self.finalizer.preflight(
            request.runId,
            request.originalQuestion,
            finalization_plan,
            publication_rows,
            assessment.model_dump(mode="json") if assessment else None,
        )
        recorded_manifest = self.gateway.record_deep_research_payload_manifest(
            job,
            worker_id,
            stage="r2a_synthesis",
            manifest=preflight.payload_manifest,
        )
        if recorded_manifest is None:
            raise LeaseLostError(
                "Worker lease expired before R2A payload manifest persistence"
            )
        synthesis_rows = list(preflight.synthesis_evidence)
        statuses = self._reserve_finalization(
            job, worker_id, request, preflight.slots
        )
        # Unused worst-case slots may have been released immediately before a
        # checkpoint response was lost. They are safe to load on resume. If a
        # cancelled slot is actually required below, _execute_final_slot still
        # blocks it from crossing the provider boundary.
        allowed_statuses = {"reserved", "completed", "cancelled"}
        blocked = {
            key: status for key, status in statuses.items()
            if status not in allowed_statuses
        }
        if blocked:
            self._cancel_final_slots(
                job, worker_id, request, preflight.slots, statuses
            )
            raise GatewayError(
                "deep_finalization_reconciliation_required",
                "A prior finalization operation requires reconciliation or new authorization",
                retryable=False,
            )
        artifacts = self._load_finalization_artifacts(job, worker_id)
        slots = {slot.key: slot for slot in preflight.slots}
        used_operation_ids: list[UUID] = []

        def execute(
            key: str,
            artifact_key: str,
            invoke: Callable[[], tuple[Mapping[str, Any], Any | None]],
        ) -> Mapping[str, Any]:
            value = self._execute_final_slot(
                job,
                worker_id,
                request,
                slots[key],
                statuses[key],
                artifacts,
                artifact_key,
                invoke,
            )
            statuses[key] = "completed"
            used_operation_ids.append(_operation_id(request.runId, key))
            return value

        try:
            def synthesize_current() -> tuple[Mapping[str, Any], Any | None]:
                call = self.finalizer.synthesis_call(
                    request.originalQuestion,
                    finalization_plan,
                    synthesis_rows,
                )
                accepted = self.finalizer.accept_synthesis_call(
                    call,
                    synthesis_rows,
                    original_evidence=publication_rows,
                )
                return accepted.model_dump(), call

            draft_json = execute(
                "final:synthesize:v1",
                "initial",
                synthesize_current,
            )
            draft = self.finalizer.validate_draft(draft_json)
            if state.executionPhase == "synthesizing":
                refreshed = self._load(job, worker_id)
                state = state.model_copy(update={
                    "checkpointVersion": state.checkpointVersion + 1,
                    "executionPhase": "verifying",
                    "operationIds": list(dict.fromkeys([
                        *state.operationIds, *used_operation_ids,
                    ])),
                    "budget": _budget(
                        refreshed.get("budget"), request.effectiveLimits.maxCostUsd
                    ),
                })
                self._checkpoint(
                    job, worker_id, state, plan_id=plan.planId,
                )

            results: list[VerificationResult] = []
            for index, claim in enumerate(self.finalizer.claims(draft)):
                key = f"final:verify:{index}"

                def verify_current(
                    claim: Any = claim,
                ) -> tuple[Mapping[str, Any], Any | None]:
                    result, call = self.finalizer.verification_call(
                        claim, synthesis_rows, reverify=False
                    )
                    return result.model_dump(), call

                result_json = execute(key, claim.claimId, verify_current)
                results.append(VerificationResult.model_validate(result_json))
            gate = self.finalizer.gate(draft, results)
            repaired = False

            if not gate.can_publish and self.finalizer.should_repair(results):
                if state.executionPhase == "verifying":
                    refreshed = self._load(job, worker_id)
                    state = state.model_copy(update={
                        "checkpointVersion": state.checkpointVersion + 1,
                        "executionPhase": "repairing",
                        "operationIds": list(dict.fromkeys([
                            *state.operationIds, *used_operation_ids,
                        ])),
                        "budget": _budget(
                            refreshed.get("budget"), request.effectiveLimits.maxCostUsd
                        ),
                    })
                    self._checkpoint(job, worker_id, state, plan_id=plan.planId)

                def repair_current() -> tuple[Mapping[str, Any], Any | None]:
                    call = self.finalizer.repair_call(
                        request.originalQuestion,
                        finalization_plan,
                        synthesis_rows,
                        draft,
                        results,
                    )
                    accepted = self.finalizer.accept_repair_call(
                        call,
                        draft,
                        results,
                        synthesis_rows,
                        original_evidence=publication_rows,
                    )
                    return accepted.model_dump(), call

                repair_json = execute(
                    "final:repair:v1",
                    "bounded-repair",
                    repair_current,
                )
                repaired_draft = self.finalizer.validate_draft(repair_json)
                draft = repaired_draft
                repaired = True
                refreshed = self._load(job, worker_id)
                state = state.model_copy(update={
                    "checkpointVersion": state.checkpointVersion + 1,
                    "executionPhase": "verifying",
                    "operationIds": list(dict.fromkeys([
                        *state.operationIds, *used_operation_ids,
                    ])),
                    "budget": _budget(
                        refreshed.get("budget"), request.effectiveLimits.maxCostUsd
                    ),
                })
                self._checkpoint(job, worker_id, state, plan_id=plan.planId)

                results = []
                for index, claim in enumerate(self.finalizer.claims(draft)):
                    key = f"final:reverify:{index}"

                    def reverify_current(
                        claim: Any = claim,
                    ) -> tuple[Mapping[str, Any], Any | None]:
                        result, call = self.finalizer.verification_call(
                            claim, synthesis_rows, reverify=True
                        )
                        return result.model_dump(), call

                    result_json = execute(key, claim.claimId, reverify_current)
                    results.append(VerificationResult.model_validate(result_json))
                gate = self.finalizer.gate(draft, results)

            execution = self.finalizer.execution(
                draft, results, gate, repaired=repaired
            )
            self._cancel_final_slots(
                job, worker_id, request, preflight.slots, statuses
            )
            refreshed = self._load(job, worker_id)
            if gate.can_publish:
                if state.executionPhase != "publishing":
                    state = state.model_copy(update={
                        "checkpointVersion": state.checkpointVersion + 1,
                        "executionPhase": "publishing",
                        "operationIds": list(dict.fromkeys([
                            *state.operationIds, *used_operation_ids,
                        ])),
                        "budget": _budget(
                            refreshed.get("budget"), request.effectiveLimits.maxCostUsd
                        ),
                    })
                    self._checkpoint(job, worker_id, state, plan_id=plan.planId)
            else:
                if state.executionPhase == "publishing":
                    raise GatewayError(
                        "finalization_replay_conflict",
                        "Publishing checkpoint no longer passes its durable gate",
                        retryable=False,
                    )
                state = state.model_copy(update={
                    "checkpointVersion": state.checkpointVersion + 1,
                    "executionPhase": "terminal",
                    "operationIds": list(dict.fromkeys([
                        *state.operationIds, *used_operation_ids,
                    ])),
                    "budget": _budget(
                        refreshed.get("budget"), request.effectiveLimits.maxCostUsd
                    ),
                    "stopReason": state.retrievalStopReason,
                    "outcome": "insufficient",
                })
                self._checkpoint(job, worker_id, state, plan_id=plan.planId)
            return execution, state
        except LeaseLostError:
            raise
        except Exception:
            try:
                self._cancel_final_slots(
                    job, worker_id, request, preflight.slots, statuses
                )
            except LeaseLostError:
                pass
            raise

    def _retrieve(
        self, job: Any, worker_id: str, request: ResearchRequestV1, query: str,
        *, round_number: int,
    ) -> tuple[list[EvidenceRecordV1], UUID, str]:
        fingerprint = query_fingerprint(query)
        key = f"round:{round_number}:internal:{fingerprint}"
        operation_id, status = self._reserve_start(
            job, worker_id, request, key, "search_internal", "r1",
            {"query": query, "round": round_number},
        )
        if status != ADMITTED_OPERATION_STATUS:
            return [], operation_id, status
        try:
            result = self.retrieval.retrieve_for_job(
                job, worker_id, query,
                scope_asset_ids=[str(value) for value in request.internalKnowledgeScope.selectedAssetIds]
                if request.internalKnowledgeScope.selectedAssetIds else None,
                top_k=12, candidate_limit=40,
            )
            _, rows = _validate_retrieval_result(result)
            records = _evidence_records(list(rows), request)
            if records:
                payload = [{
                    "id": str(item.evidenceId),
                    "assetId": str(item.provenance.assetId),
                    "revisionId": str(item.provenance.revisionId),
                    "retrievalMethod": str(item.sourceMetadata.get("retrievalMethod", "lexical")),
                    "retrievalScore": max(0.0, float(item.sourceMetadata.get("retrievalScore", 0))),
                    "queryHits": 1,
                } for item in records]
                if self.gateway.append_deep_research_evidence(
                    job, worker_id, operation_id=str(operation_id),
                    round_number=round_number, query_fingerprint=fingerprint,
                    evidence=payload,
                ) is None:
                    raise LeaseLostError("Worker lease is no longer valid")
            self._settle(job, worker_id, operation_id)
            return records, operation_id, "completed"
        except LeaseLostError:
            raise
        except Exception:
            self._settle(job, worker_id, operation_id, status="failed", error_code="operation_failed")
            raise

    def _web_retrieve(
        self, job: Any, worker_id: str, request: ResearchRequestV1, query: str,
        *, round_number: int,
    ) -> tuple[list[EvidenceRecordV1], UUID, str]:
        if self.web_provider is None:
            return [], _operation_id(request.runId, f"round:{round_number}:web:unavailable"), "unavailable"
        fingerprint = query_fingerprint(query)
        key = f"round:{round_number}:web:{fingerprint}"
        operation_id, status = self._reserve_start(
            job, worker_id, request, key, "discover_web", "web",
            {"query": query, "round": round_number}, self.web_operation_estimate_usd,
        )
        if status != ADMITTED_OPERATION_STATUS:
            return [], operation_id, status
        try:
            captures = discover_and_fetch(self.web_provider, request, query)
            records = [item.record for item in captures]
            if records and self.gateway.append_deep_web_evidence(
                job, worker_id, operation_id=str(operation_id),
                evidence=[item.model_dump(mode="json") for item in records],
            ) is None:
                raise LeaseLostError("Worker lease is no longer valid")
            self._settle(job, worker_id, operation_id)
            return records, operation_id, "completed"
        except LeaseLostError:
            raise
        except Exception:
            self._settle(
                job, worker_id, operation_id, status="outcome_unknown",
                error_code="web_outcome_unknown",
            )
            raise

    def process_claimed(self, job: Any, worker_id: str, lease_seconds: int) -> ProcessResult:
        try:
            with LeaseGuard(self.gateway, job, worker_id, lease_seconds) as lease:
                bundle = self._load(job, worker_id)
                request = ResearchRequestV1.model_validate(bundle.get("request"))
                if str(request.runId) != job.payload.input.get("researchRunId") or str(request.userId) != job.user_id:
                    raise GatewayError("invalid_deep_request", "Deep Research identity is invalid", retryable=False)
                plan = ResearchPlanV1.model_validate(bundle["plan"]) if bundle.get("plan") else None
                records = _evidence_records(list(bundle.get("evidence") or []), request)
                state = ResearchIterationStateV1.model_validate(bundle["state"]) if bundle.get("state") else None

                if state is None or state.executionPhase in {
                    "planning", "retrieving", "assessing", "deciding"
                }:
                    protected_budget = self._ensure_finalization_reserve(
                        job, worker_id
                    )
                    if protected_budget is not None:
                        bundle = dict(bundle)
                        bundle["budget"] = protected_budget

                if plan is None:
                    if self._deadline_exhausted(bundle):
                        result = self.gateway.complete_deep_research_without_publication(
                            job, worker_id, outcome="insufficient",
                            stop_reason="duration_exhausted",
                        )
                        if result is None:
                            raise LeaseLostError("Worker lease is no longer valid")
                        return ProcessResult("completed", job.id)
                    context = build_planning_context_pack(
                        [item.model_dump(mode="json") | {
                            "id": str(item.evidenceId), "content": item.excerpt,
                            "assetId": str(item.provenance.assetId),
                            "revisionId": str(item.provenance.revisionId),
                            "locator": item.citationLocator,
                        } for item in records], request
                    )
                    planning_estimate = self.planner.max_operation_cost(
                        request, context
                    ) if hasattr(self.planner, "max_operation_cost") else ZERO
                    operation_id, status = self._reserve_start(
                        job, worker_id, request, "plan:v1", "plan_research", "r2b",
                        {"objective": request.objective},
                        planning_estimate,
                    )
                    if status == "budget_exhausted":
                        raise GatewayError("deep_budget_exhausted", "Deep Research budget is exhausted", retryable=False)
                    if status == "completed":
                        raise GatewayError(
                            "missing_completed_plan",
                            "Completed R2B operation has no durable Research Plan",
                            retryable=False,
                        )
                    if status in {
                        *REAUTHORIZATION_REQUIRED_STATUSES,
                        RUNNING_RECONCILIATION_STATUS,
                    }:
                        raise GatewayError(
                            "deep_operation_reauthorization_required",
                            "R2B operation is terminal and requires a newly authorized operation",
                            retryable=False,
                        )
                    if status != ADMITTED_OPERATION_STATUS:
                        raise GatewayError(
                            "invalid_operation_state",
                            "R2B operation is not admitted to the provider boundary",
                            retryable=False,
                        )
                    try:
                        def persist_r2b_diagnostic(diagnostic: Any) -> None:
                            recorded = self.gateway.record_deep_research_validation_diagnostic(
                                job,
                                worker_id,
                                operation_id=str(operation_id),
                                diagnostic=diagnostic.model_dump(mode="json"),
                            )
                            if recorded is None:
                                raise LeaseLostError(
                                    "Worker lease expired before validation diagnostic persistence"
                                )

                        planned = self.planner.plan(
                            request,
                            context,
                            diagnostic_sink=persist_r2b_diagnostic,
                        )
                    except Exception as error:
                        from personal_brain.research.deep_llm import DeepProviderError
                        if not isinstance(error, DeepProviderError):
                            raise
                        settlement = "outcome_unknown" if error.outcome_unknown else "failed"
                        self._settle(
                            job, worker_id, operation_id,
                            status=settlement,
                            actual_cost_usd=error.known_cost_usd,
                            error_code=f"r2b_{error.error_kind}",
                            result_ref=_provider_result_ref(
                                error.request_ids, error.usage, error.finish_reasons
                            ),
                        )
                        raise GatewayError(
                            "deep_provider_outcome_unknown" if error.outcome_unknown
                            else "deep_provider_error",
                            "R2B provider execution did not produce a usable result",
                            retryable=False,
                        ) from error
                    lease.pulse()
                    if self.require_provider_success and planned.fallback_used:
                        self._settle(
                            job, worker_id, operation_id,
                            status="failed",
                            actual_cost_usd=planned.actual_cost_usd,
                            error_code="r2b_fallback_rejected",
                            result_ref=_provider_result_ref(
                                planned.request_ids, planned.usage,
                                planned.finish_reasons,
                            ),
                        )
                        raise GatewayError(
                            "deep_provider_fallback_rejected",
                            "R2B fallback cannot satisfy a provider-required run",
                            retryable=False,
                        )
                    if self._deadline_exhausted(self._load(job, worker_id)):
                        self._settle(
                            job, worker_id, operation_id,
                            actual_cost_usd=planned.actual_cost_usd,
                            error_code="duration_exhausted",
                            result_ref=_provider_result_ref(
                                planned.request_ids, planned.usage,
                                planned.finish_reasons,
                            ),
                        )
                        result = self.gateway.complete_deep_research_without_publication(
                            job, worker_id, outcome="insufficient",
                            stop_reason="duration_exhausted",
                        )
                        if result is None:
                            raise LeaseLostError("Worker lease is no longer valid")
                        return ProcessResult("completed", job.id)
                    saved = self.gateway.record_deep_research_plan(
                        job, worker_id, plan=planned.plan.model_dump(mode="json"),
                        quality_gate=planned.quality.gate,
                        model_provider=planned.model_provider, model_name=planned.model_name,
                        prompt_version=planned.prompt_version,
                    )
                    if saved is None:
                        raise LeaseLostError("Worker lease is no longer valid")
                    plan = planned.plan
                    self._settle(
                        job, worker_id, operation_id,
                        actual_cost_usd=planned.actual_cost_usd,
                        error_code="r2b_fallback" if planned.fallback_used else None,
                        result_ref=_provider_result_ref(
                            planned.request_ids, planned.usage,
                            planned.finish_reasons,
                        ),
                    )
                    state = ResearchIterationStateV1(
                        schemaVersion="research-iteration-state-v1", runId=request.runId,
                        planVersion=plan.planVersion, checkpointVersion=1, currentRound=0,
                        completedRounds=[], executionPhase="planning", queryFingerprints=[],
                        operationIds=[operation_id], assessmentIds=[],
                        requirementProgress={item.id: "unknown" for item in plan.evidenceRequirements},
                        unresolvedCriticalGapIds=[], proposedActions=[], approvedActionIds=[],
                        budget=_budget(bundle.get("budget"), request.effectiveLimits.maxCostUsd),
                        consecutiveNoInformationGainRounds=0,
                    )
                    self._checkpoint(job, worker_id, state, plan_id=plan.planId)
                elif state is None:
                    state = ResearchIterationStateV1(
                        schemaVersion="research-iteration-state-v1", runId=request.runId,
                        planVersion=plan.planVersion, checkpointVersion=1, currentRound=0,
                        completedRounds=[], executionPhase="planning", queryFingerprints=[],
                        operationIds=[], assessmentIds=[],
                        requirementProgress={item.id: "unknown" for item in plan.evidenceRequirements},
                        unresolvedCriticalGapIds=[], proposedActions=[], approvedActionIds=[],
                        budget=_budget(bundle.get("budget"), request.effectiveLimits.maxCostUsd),
                        consecutiveNoInformationGainRounds=0,
                    )
                    self._checkpoint(job, worker_id, state, plan_id=plan.planId)
                assert plan is not None and state is not None

                assessment = EvidenceAssessmentV1.model_validate(bundle["assessment"]) if bundle.get("assessment") else None
                while state.executionPhase in {
                    "planning", "retrieving", "assessing", "deciding"
                }:
                    lease.pulse()
                    current = self._load(job, worker_id)
                    if self._deadline_exhausted(current):
                        state = self._terminal_state(state, reason="duration_exhausted")
                        self._checkpoint(
                            job, worker_id, state, plan_id=plan.planId,
                            assessment_id=assessment.assessmentId if assessment else None,
                        )
                        break
                    if state.executionPhase in {"planning", "deciding"}:
                        if len(state.queryFingerprints) >= request.effectiveLimits.maxQueries:
                            state = (
                                self._begin_finalization(
                                    state,
                                    reason="max_queries_exhausted",
                                    outcome="qualified_partial",
                                )
                                if self.finalizer is not None and records
                                else self._terminal_state(
                                    state, reason="max_queries_exhausted"
                                )
                            )
                            self._checkpoint(
                                job, worker_id, state, plan_id=plan.planId,
                                assessment_id=assessment.assessmentId if assessment else None,
                            )
                            break
                        if state.executionPhase == "planning":
                            action = _next_planned_retrieval_action(
                                request,
                                plan,
                                state.queryFingerprints,
                                round_number=state.currentRound + 1,
                            )
                            if action is None:
                                action = ResearchActionV1(
                                    actionId=_operation_id(
                                        request.runId,
                                        f"action:{state.currentRound + 1}:objective",
                                    ),
                                    actionType="search_internal",
                                    reason="Use the authoritative question when no R2B proposal is available",
                                    query=request.originalQuestion,
                                    gapIds=[],
                                )
                        else:
                            assert assessment is not None
                            decision = self.controller.decide(request, plan, assessment, state)
                            action = decision.action
                            if action.actionType == "stop_retrieval":
                                state = (
                                    self._begin_finalization(
                                        state,
                                        reason=decision.stop_reason,
                                        outcome=decision.outcome,
                                    )
                                    if self.finalizer is not None and records
                                    else state.model_copy(update={
                                        "checkpointVersion": state.checkpointVersion + 1,
                                        "executionPhase": "terminal",
                                        "stopReason": decision.stop_reason,
                                        "outcome": decision.outcome,
                                        "proposedActions": [action],
                                        "approvedActionIds": [],
                                    })
                                )
                                self._checkpoint(job, worker_id, state, plan_id=plan.planId,
                                                 assessment_id=assessment.assessmentId)
                                break
                        state = state.model_copy(update={
                            "checkpointVersion": state.checkpointVersion + 1,
                            "currentRound": state.currentRound + 1,
                            "executionPhase": "retrieving",
                            "proposedActions": [action], "approvedActionIds": [action.actionId],
                        })
                        self._checkpoint(job, worker_id, state, plan_id=plan.planId,
                                         assessment_id=assessment.assessmentId if assessment else None)

                    before = dict(state.requirementProgress)
                    if state.executionPhase == "retrieving":
                        action = state.proposedActions[0]
                        if action.actionType not in {"search_internal", "discover_web"} or not action.query:
                            state = (
                                self._begin_finalization(
                                    state,
                                    reason="no_permitted_action",
                                    outcome="qualified_partial",
                                )
                                if self.finalizer is not None and records
                                else state.model_copy(update={
                                    "checkpointVersion": state.checkpointVersion + 1,
                                    "executionPhase": "terminal",
                                    "stopReason": "no_permitted_action",
                                    "outcome": "qualified_partial",
                                })
                            )
                            self._checkpoint(job, worker_id, state, plan_id=plan.planId)
                            break
                        if action.actionType == "search_internal":
                            _, operation_id, op_status = self._retrieve(
                                job, worker_id, request, action.query, round_number=state.currentRound
                            )
                        else:
                            _, operation_id, op_status = self._web_retrieve(
                                job, worker_id, request, action.query, round_number=state.currentRound
                            )
                        if op_status == "budget_exhausted":
                            state = (
                                self._begin_finalization(
                                    state,
                                    reason="budget_exhausted",
                                    outcome="qualified_partial",
                                )
                                if self.finalizer is not None and records
                                else state.model_copy(update={
                                    "checkpointVersion": state.checkpointVersion + 1,
                                    "executionPhase": "terminal",
                                    "stopReason": "budget_exhausted",
                                    "outcome": "qualified_partial",
                                })
                            )
                            self._checkpoint(job, worker_id, state, plan_id=plan.planId)
                            break
                        if op_status in {
                            *REAUTHORIZATION_REQUIRED_STATUSES,
                            RUNNING_RECONCILIATION_STATUS,
                        }:
                            state = self._terminal_state(
                                state, reason="authorization_blocked"
                            )
                            self._checkpoint(
                                job, worker_id, state, plan_id=plan.planId,
                                assessment_id=assessment.assessmentId if assessment else None,
                            )
                            break
                        refreshed = self._load(job, worker_id)
                        records = _evidence_records(list(refreshed.get("evidence") or []), request)
                        query_fingerprints = list(dict.fromkeys([
                            *state.queryFingerprints,
                            query_fingerprint(action.query),
                        ]))
                        next_action = None
                        if len(query_fingerprints) < request.effectiveLimits.maxQueries:
                            next_action = _next_planned_retrieval_action(
                                request,
                                plan,
                                query_fingerprints,
                                round_number=state.currentRound,
                            )
                        state = state.model_copy(update={
                            "checkpointVersion": state.checkpointVersion + 1,
                            "executionPhase": "retrieving" if next_action else "assessing",
                            "queryFingerprints": query_fingerprints,
                            "operationIds": list(dict.fromkeys([*state.operationIds, operation_id])),
                            "proposedActions": [next_action] if next_action else [],
                            "approvedActionIds": [next_action.actionId] if next_action else [],
                            "budget": _budget(refreshed.get("budget"), request.effectiveLimits.maxCostUsd),
                        })
                        self._checkpoint(job, worker_id, state, plan_id=plan.planId)
                        if next_action is not None:
                            continue

                    current = self._load(job, worker_id)
                    if self._deadline_exhausted(current):
                        state = self._terminal_state(state, reason="duration_exhausted")
                        self._checkpoint(
                            job, worker_id, state, plan_id=plan.planId,
                            assessment_id=assessment.assessmentId if assessment else None,
                        )
                        break

                    try:
                        evaluation_estimate = self.evaluator.max_operation_cost(
                            plan, records, user_id=request.userId
                        ) if hasattr(self.evaluator, "max_operation_cost") else ZERO
                    except Exception as error:
                        from personal_brain.research.deep_evaluation import (
                            EvidencePayloadLimitError,
                        )
                        if not isinstance(error, EvidencePayloadLimitError):
                            raise
                        state = self._terminal_state(
                            state, reason="authorization_blocked"
                        )
                        self._checkpoint(
                            job, worker_id, state, plan_id=plan.planId,
                            assessment_id=assessment.assessmentId if assessment else None,
                        )
                        break
                    evaluation_id, evaluation_status = self._reserve_start(
                        job, worker_id, request, f"round:{state.currentRound}:evaluate",
                        "evaluate_evidence", "r2c",
                        {"planId": str(plan.planId), "round": state.currentRound,
                         "evidenceIds": sorted(str(item.evidenceId) for item in records)},
                        evaluation_estimate,
                    )
                    if evaluation_status == "budget_exhausted":
                        state = (
                            self._begin_finalization(
                                state,
                                reason="budget_exhausted",
                                outcome="qualified_partial",
                            )
                            if self.finalizer is not None and records
                            else state.model_copy(update={
                                "checkpointVersion": state.checkpointVersion + 1,
                                "executionPhase": "terminal",
                                "stopReason": "budget_exhausted",
                                "outcome": "qualified_partial",
                            })
                        )
                        self._checkpoint(job, worker_id, state, plan_id=plan.planId)
                        break
                    if evaluation_status in {
                        *REAUTHORIZATION_REQUIRED_STATUSES,
                        RUNNING_RECONCILIATION_STATUS,
                    }:
                        state = state.model_copy(update={
                            "checkpointVersion": state.checkpointVersion + 1,
                            "executionPhase": "terminal", "stopReason": "authorization_blocked",
                            "outcome": "qualified_partial",
                        })
                        self._checkpoint(job, worker_id, state, plan_id=plan.planId)
                        break
                    if evaluation_status == "completed" and assessment is None:
                        raise GatewayError(
                            "missing_completed_assessment",
                            "Completed R2C operation has no durable assessment",
                            retryable=False,
                        )
                    if evaluation_status not in {ADMITTED_OPERATION_STATUS, "completed"}:
                        raise GatewayError(
                            "invalid_operation_state",
                            "R2C operation is not admitted to the provider boundary",
                            retryable=False,
                        )
                    if evaluation_status == ADMITTED_OPERATION_STATUS:
                        try:
                            def persist_r2c_diagnostic(diagnostic: Any) -> None:
                                recorded = self.gateway.record_deep_research_evaluation_diagnostic(
                                    job,
                                    worker_id,
                                    operation_id=str(evaluation_id),
                                    diagnostic=diagnostic.model_dump(mode="json"),
                                )
                                if recorded is None:
                                    raise LeaseLostError(
                                        "Worker lease expired before R2C validation diagnostic persistence"
                                    )

                            evaluated = self.evaluator.assess(
                                plan,
                                records,
                                user_id=request.userId,
                                diagnostic_sink=persist_r2c_diagnostic,
                            )
                        except Exception as error:
                            from personal_brain.research.deep_llm import DeepProviderError
                            if not isinstance(error, DeepProviderError):
                                raise
                            settlement = "outcome_unknown" if error.outcome_unknown else "failed"
                            self._settle(
                                job, worker_id, evaluation_id,
                                status=settlement,
                                actual_cost_usd=error.known_cost_usd,
                                error_code=f"r2c_{error.error_kind}",
                                result_ref=_provider_result_ref(
                                    error.request_ids, error.usage,
                                    error.finish_reasons,
                                ),
                            )
                            raise GatewayError(
                                "deep_provider_outcome_unknown" if error.outcome_unknown
                                else "deep_provider_error",
                                "R2C provider execution did not produce a usable result",
                                retryable=False,
                            ) from error
                        lease.pulse()
                        if self.require_provider_success and evaluated.fallback_used:
                            self._settle(
                                job, worker_id, evaluation_id,
                                status="failed",
                                actual_cost_usd=evaluated.actual_cost_usd,
                                error_code="r2c_fallback_rejected",
                                result_ref=_provider_result_ref(
                                    evaluated.request_ids, evaluated.usage,
                                    evaluated.finish_reasons,
                                ),
                            )
                            raise GatewayError(
                                "deep_provider_fallback_rejected",
                                "R2C fallback cannot satisfy a provider-required run",
                                retryable=False,
                            )
                        if self._deadline_exhausted(self._load(job, worker_id)):
                            self._settle(
                                job, worker_id, evaluation_id,
                                actual_cost_usd=evaluated.actual_cost_usd,
                                error_code="duration_exhausted",
                                result_ref=_provider_result_ref(
                                    evaluated.request_ids, evaluated.usage,
                                    evaluated.finish_reasons,
                                ),
                            )
                            state = self._terminal_state(
                                state, reason="duration_exhausted"
                            )
                            self._checkpoint(
                                job, worker_id, state, plan_id=plan.planId,
                                assessment_id=assessment.assessmentId if assessment else None,
                            )
                            break
                        assessment = evaluated.assessment
                        if self.gateway.record_deep_research_assessment(
                            job, worker_id, assessment.model_dump(mode="json")
                        ) is None:
                            raise LeaseLostError("Worker lease is no longer valid")
                        self._settle(
                            job, worker_id, evaluation_id,
                            actual_cost_usd=evaluated.actual_cost_usd,
                            error_code="r2c_fallback" if evaluated.fallback_used else None,
                            result_ref=_provider_result_ref(
                                evaluated.request_ids, evaluated.usage,
                                evaluated.finish_reasons,
                            ),
                        )
                    assert assessment is not None
                    progress = {item.requirementId: item.status for item in assessment.requirementCoverage}
                    gain = material_coverage_gain(before, progress)
                    state = state.model_copy(update={
                        "checkpointVersion": state.checkpointVersion + 1,
                        "executionPhase": "deciding",
                        "completedRounds": list(dict.fromkeys([*state.completedRounds, state.currentRound])),
                        "assessmentIds": list(dict.fromkeys([*state.assessmentIds, assessment.assessmentId])),
                        "operationIds": list(dict.fromkeys([*state.operationIds, evaluation_id])),
                        "requirementProgress": progress,
                        "unresolvedCriticalGapIds": [g.id for g in assessment.prioritizedGaps if g.severity == "critical"],
                        "consecutiveNoInformationGainRounds": 0 if gain else state.consecutiveNoInformationGainRounds + 1,
                        "proposedActions": [], "approvedActionIds": [],
                    })
                    self._checkpoint(job, worker_id, state, plan_id=plan.planId,
                                     assessment_id=assessment.assessmentId)

                if state.executionPhase in {
                    "synthesizing", "verifying", "repairing", "publishing"
                } and records:
                    publication_rows = _publication_rows(records)
                    execution, state = self._run_finalization(
                        job, worker_id, request, plan, records, state, assessment
                    )
                    if execution.gate.can_publish:
                        draft = adapt_r2_publication(execution, publication_rows)
                        if state.targetOutcome == "complete":
                            publish = (
                                self.gateway.publish_deep_research_draft
                                if any(item.origin == "web" for item in records)
                                else self.gateway.publish_internal_deep_research_draft
                            )
                            published = publish(
                                job, worker_id, model_provider=self.finalizer.provider,
                                model_name=self.finalizer.model_name,
                                prompt_version="deep-r2ef-v1", output_hash=draft.output_hash,
                                title=draft.title, body=draft.body, findings=draft.findings,
                            )
                            if published is None:
                                raise LeaseLostError("Worker lease expired before publication")
                            return ProcessResult("completed", job.id, note_id=published.get("noteId"))
                        recorded = self.gateway.record_deep_research_result(
                            job, worker_id, result_status="verified_partial",
                            stop_reason=state.retrievalStopReason or "max_rounds_reached",
                            title=draft.title, body=draft.body, findings=draft.findings,
                            verification={"gate": "passed", "published": False},
                            output_hash=draft.output_hash,
                        )
                        if recorded is None:
                            raise LeaseLostError("Worker lease expired before result persistence")

                outcome = (
                    "qualified_partial"
                    if (
                        state.executionPhase == "publishing"
                        and state.targetOutcome == "qualified_partial"
                    ) or (
                        self.finalizer is None
                        and state.outcome in {"complete", "qualified_partial"}
                    )
                    else "insufficient"
                )
                if outcome == "insufficient" and self.gateway.record_deep_research_result(
                    job, worker_id, result_status="insufficient",
                    stop_reason=(
                        state.stopReason or state.retrievalStopReason
                        or "no_permitted_action"
                    ),
                    title=None, body=None, findings=[],
                    verification={"gate": "not_passed", "published": False},
                    output_hash=None,
                ) is None:
                    raise LeaseLostError("Worker lease expired before result persistence")
                result = self.gateway.complete_deep_research_without_publication(
                    job, worker_id, outcome=outcome,
                    stop_reason=(
                        state.stopReason or state.retrievalStopReason
                        or "no_permitted_action"
                    ),
                )
                if result is None:
                    raise LeaseLostError("Worker lease is no longer valid")
                return ProcessResult("completed", job.id)
        except LeaseLostError:
            return ProcessResult("lease_lost", job.id)
        except GatewayError:
            raise
        except FinalizationPayloadLimitError as error:
            raise GatewayError(
                "deep_finalization_payload_limit",
                str(error),
                retryable=False,
            ) from error
        except (ValueError, TypeError, KeyError) as error:
            raise GatewayError(
                "invalid_deep_research_state",
                "Durable Deep Research state is invalid",
                retryable=False,
            ) from error
