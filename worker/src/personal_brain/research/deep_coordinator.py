"""Durable, lease-fenced execution for Deep Research R2B/R2C/R2D."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from uuid import UUID, NAMESPACE_URL, uuid5

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
from personal_brain.research.deep_planning import (
    DeepResearchPlanner,
    build_planning_context_pack,
)
from personal_brain.research.publication_adapter import adapt_r2_publication
from personal_brain.research_agent import _validate_retrieval_result
from personal_brain.supabase_gateway import GatewayError


ZERO = Decimal("0")


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


def _operation_id(run_id: UUID, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"personal-brain:{run_id}:{key}")


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


def _internal_evidence(
    rows: list[Mapping[str, Any]], request: ResearchRequestV1
) -> list[EvidenceRecordV1]:
    records: list[EvidenceRecordV1] = []
    seen: set[UUID] = set()
    now = datetime.now(timezone.utc)
    for row in rows:
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
        "assetId": str(item.provenance.assetId),
        "revisionId": str(item.provenance.revisionId),
        "excerpt": item.excerpt,
        "locator": item.citationLocator,
    } for item in records if item.citationEligible and item.validityStatus == "valid"]


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
    ) -> None:
        self.gateway = gateway
        self.retrieval = retrieval
        self.planner = planner or DeepResearchPlanner()
        self.evaluator = evaluator or EvidenceEvaluator()
        self.controller = controller or ResearchIterationController()
        self.orchestrator = orchestrator

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
        if status in {"completed", "outcome_unknown", "failed", "cancelled", "abandoned"}:
            return operation_id, str(status)
        if status not in {"reserved", "running"}:
            raise GatewayError("invalid_operation_state", "Research operation state is invalid", retryable=False)
        if status == "reserved" and not self.gateway.start_deep_research_operation(
            job, worker_id, str(operation_id)
        ):
            raise LeaseLostError("Worker lease is no longer valid")
        return operation_id, "running"

    def _settle(
        self, job: Any, worker_id: str, operation_id: UUID, *,
        status: str = "completed", error_code: str | None = None,
    ) -> None:
        if self.gateway.settle_deep_research_operation(
            job, worker_id, operation_id=str(operation_id), status=status,
            actual_cost_usd="0", error_code=error_code,
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
        if status in {"completed", "outcome_unknown"}:
            return [], operation_id, status
        if status == "budget_exhausted":
            return [], operation_id, status
        try:
            result = self.retrieval.retrieve_for_job(
                job, worker_id, query,
                scope_asset_ids=[str(value) for value in request.internalKnowledgeScope.selectedAssetIds]
                if request.internalKnowledgeScope.selectedAssetIds else None,
                top_k=12, candidate_limit=40,
            )
            _, rows = _validate_retrieval_result(result)
            records = _internal_evidence(list(rows), request)
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

    def process_claimed(self, job: Any, worker_id: str, lease_seconds: int) -> ProcessResult:
        try:
            with LeaseGuard(self.gateway, job, worker_id, lease_seconds) as lease:
                bundle = self._load(job, worker_id)
                request = ResearchRequestV1.model_validate(bundle.get("request"))
                if str(request.runId) != job.payload.input.get("researchRunId") or str(request.userId) != job.user_id:
                    raise GatewayError("invalid_deep_request", "Deep Research identity is invalid", retryable=False)
                plan = ResearchPlanV1.model_validate(bundle["plan"]) if bundle.get("plan") else None
                records = _internal_evidence(list(bundle.get("evidence") or []), request)
                state = ResearchIterationStateV1.model_validate(bundle["state"]) if bundle.get("state") else None

                if plan is None:
                    operation_id, status = self._reserve_start(
                        job, worker_id, request, "plan:v1", "plan_research", "r2b",
                        {"objective": request.objective},
                    )
                    if status == "budget_exhausted":
                        raise GatewayError("deep_budget_exhausted", "Deep Research budget is exhausted", retryable=False)
                    if status == "outcome_unknown":
                        raise GatewayError("deep_operation_uncertain", "Planning outcome is uncertain", retryable=False)
                    context = build_planning_context_pack(
                        [item.model_dump(mode="json") | {
                            "id": str(item.evidenceId), "content": item.excerpt,
                            "assetId": str(item.provenance.assetId),
                            "revisionId": str(item.provenance.revisionId),
                            "locator": item.citationLocator,
                        } for item in records], request
                    )
                    planned = self.planner.plan(request, context)
                    saved = self.gateway.record_deep_research_plan(
                        job, worker_id, plan=planned.plan.model_dump(mode="json"),
                        quality_gate=planned.quality.gate,
                        model_provider=planned.model_provider, model_name=planned.model_name,
                        prompt_version=planned.prompt_version,
                    )
                    if saved is None:
                        raise LeaseLostError("Worker lease is no longer valid")
                    plan = planned.plan
                    self._settle(job, worker_id, operation_id)
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
                while state.executionPhase != "terminal":
                    lease.pulse()
                    if state.executionPhase in {"planning", "deciding"}:
                        if state.executionPhase == "planning":
                            query = next((p.query for p in plan.retrievalProposals if p.source == "internal"), request.originalQuestion)
                            action = ResearchActionV1(
                                actionId=_operation_id(request.runId, f"action:{state.currentRound + 1}:{query_fingerprint(query)}"),
                                actionType="search_internal", reason="Execute the next authorized internal query",
                                query=query, gapIds=[],
                            )
                        else:
                            assert assessment is not None
                            decision = self.controller.decide(request, plan, assessment, state)
                            action = decision.action
                            if action.actionType == "stop_retrieval":
                                state = state.model_copy(update={
                                    "checkpointVersion": state.checkpointVersion + 1,
                                    "executionPhase": "terminal", "stopReason": decision.stop_reason,
                                    "outcome": decision.outcome,
                                    "proposedActions": [action], "approvedActionIds": [],
                                })
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
                        if action.actionType != "search_internal" or not action.query:
                            state = state.model_copy(update={
                                "checkpointVersion": state.checkpointVersion + 1,
                                "executionPhase": "terminal", "stopReason": "no_permitted_action",
                                "outcome": "qualified_partial",
                            })
                            self._checkpoint(job, worker_id, state, plan_id=plan.planId)
                            break
                        _, operation_id, op_status = self._retrieve(
                            job, worker_id, request, action.query, round_number=state.currentRound
                        )
                        if op_status == "budget_exhausted":
                            state = state.model_copy(update={
                                "checkpointVersion": state.checkpointVersion + 1,
                                "executionPhase": "terminal", "stopReason": "budget_exhausted",
                                "outcome": "qualified_partial",
                            })
                            self._checkpoint(job, worker_id, state, plan_id=plan.planId)
                            break
                        refreshed = self._load(job, worker_id)
                        records = _internal_evidence(list(refreshed.get("evidence") or []), request)
                        state = state.model_copy(update={
                            "checkpointVersion": state.checkpointVersion + 1,
                            "executionPhase": "assessing",
                            "queryFingerprints": list(dict.fromkeys([*state.queryFingerprints, query_fingerprint(action.query)])),
                            "operationIds": list(dict.fromkeys([*state.operationIds, operation_id])),
                            "budget": _budget(refreshed.get("budget"), request.effectiveLimits.maxCostUsd),
                        })
                        self._checkpoint(job, worker_id, state, plan_id=plan.planId)

                    evaluation_id, evaluation_status = self._reserve_start(
                        job, worker_id, request, f"round:{state.currentRound}:evaluate",
                        "evaluate_evidence", "r2c",
                        {"planId": str(plan.planId), "round": state.currentRound,
                         "evidenceIds": sorted(str(item.evidenceId) for item in records)},
                    )
                    if evaluation_status == "budget_exhausted":
                        state = state.model_copy(update={
                            "checkpointVersion": state.checkpointVersion + 1,
                            "executionPhase": "terminal", "stopReason": "budget_exhausted",
                            "outcome": "qualified_partial",
                        })
                        self._checkpoint(job, worker_id, state, plan_id=plan.planId)
                        break
                    if evaluation_status == "outcome_unknown":
                        state = state.model_copy(update={
                            "checkpointVersion": state.checkpointVersion + 1,
                            "executionPhase": "terminal", "stopReason": "budget_exhausted",
                            "outcome": "qualified_partial",
                        })
                        self._checkpoint(job, worker_id, state, plan_id=plan.planId)
                        break
                    if evaluation_status != "completed":
                        assessment = self.evaluator.assess(
                            plan, records, user_id=request.userId
                        ).assessment
                        if self.gateway.record_deep_research_assessment(
                            job, worker_id, assessment.model_dump(mode="json")
                        ) is None:
                            raise LeaseLostError("Worker lease is no longer valid")
                        self._settle(job, worker_id, evaluation_id)
                    elif assessment is None:
                        raise GatewayError(
                            "missing_completed_assessment",
                            "Completed evaluation has no durable assessment",
                            retryable=False,
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

                if state.outcome == "complete" and self.orchestrator is not None and records:
                    publication_rows = _publication_rows(records)
                    execution = self.orchestrator.run(
                        request.originalQuestion, plan.model_dump(mode="json"), publication_rows
                    )
                    if execution.gate.can_publish:
                        draft = adapt_r2_publication(execution, publication_rows)
                        published = self.gateway.publish_research_draft(
                            job, worker_id, model_provider=self.orchestrator.generator.provider,
                            model_name=self.orchestrator.generator.model_name,
                            prompt_version="deep-r2ef-v1", output_hash=draft.output_hash,
                            title=draft.title, body=draft.body, findings=draft.findings,
                        )
                        if published is None:
                            raise LeaseLostError("Worker lease expired before publication")
                        return ProcessResult("completed", job.id, note_id=published.get("noteId"))

                outcome = "qualified_partial" if state.outcome in {"complete", "qualified_partial"} else "insufficient"
                result = self.gateway.complete_deep_research_without_publication(
                    job, worker_id, outcome=outcome,
                    stop_reason=state.stopReason or "no_permitted_action",
                )
                if result is None:
                    raise LeaseLostError("Worker lease is no longer valid")
                return ProcessResult("completed", job.id)
        except LeaseLostError:
            return ProcessResult("lease_lost", job.id)
        except GatewayError:
            raise
        except (ValueError, TypeError, KeyError) as error:
            raise GatewayError(
                "invalid_deep_research_state",
                "Durable Deep Research state is invalid",
                retryable=False,
            ) from error
