"""R2C evidence evaluation with deterministic safety checks."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid4

from pydantic import Field

from personal_brain.research.deep_contracts import (
    AssessmentOverallStatusV1,
    EvaluationTelemetryV1,
    EvaluationLevel,
    EvidenceAssessmentV1,
    EvidenceConflictV1,
    EvidenceQualityV1,
    EvidenceRecordV1,
    RequirementCoverageV1,
    ResearchGapV1,
    ResearchPlanV1,
    SubquestionAssessmentV1,
    DeepStrictModel,
)
from personal_brain.llm_client import LLMUsage


R2C_PROMPT_VERSION = "r2c-evidence-v1"
MAX_EVALUATION_EVIDENCE = 80


class EvidencePayloadLimitError(ValueError):
    pass


class EvidenceEvaluationModel(Protocol):
    provider: str
    model_name: str

    def evaluate(self, prompt: Mapping[str, Any]) -> Any: ...

    def repair(self, prompt: Mapping[str, Any]) -> Any: ...

    def max_operation_cost(self, prompt: Mapping[str, Any]) -> Decimal: ...


class ProposedEvidenceAssessment(DeepStrictModel):
    """Only fields the R2C model is allowed to author."""

    evidenceQuality: list[EvidenceQualityV1] = Field(max_length=200)
    requirementCoverage: list[RequirementCoverageV1] = Field(min_length=1, max_length=40)
    subquestionStatus: list[SubquestionAssessmentV1] = Field(min_length=1, max_length=8)
    overallStatus: AssessmentOverallStatusV1
    contradiction: EvaluationLevel
    independence: EvaluationLevel
    conflicts: list[EvidenceConflictV1] = Field(max_length=50)
    prioritizedGaps: list[ResearchGapV1] = Field(max_length=40)
    explanation: str = Field(min_length=1, max_length=4000)


@dataclass(frozen=True, slots=True)
class EvaluationUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    assessment: EvidenceAssessmentV1
    repaired: bool
    fallback_used: bool
    usage: LLMUsage = LLMUsage()
    actual_cost_usd: Decimal = Decimal("0")
    latency_ms: int = 0
    request_ids: tuple[str, ...] = ()
    finish_reasons: tuple[str, ...] = ()


def _lineage_key(record: EvidenceRecordV1) -> str:
    roots = sorted(str(value) for value in record.provenance.parentEvidenceIds)
    return roots[0] if roots else f"{record.origin}:{record.sourceIdentity}:{record.contentHash}"


def deterministic_precheck(
    records: list[EvidenceRecordV1], *, user_id: UUID, run_id: UUID
) -> tuple[list[EvidenceRecordV1], dict[UUID, str]]:
    selected: list[EvidenceRecordV1] = []
    lineage: dict[UUID, str] = {}
    seen_ids: set[UUID] = set()
    seen_versions: set[tuple[str, str]] = set()
    for record in records:
        if record.userId != user_id or record.runId != run_id:
            raise ValueError("evidence ownership or Research Run scope is invalid")
        if record.evidenceId in seen_ids:
            raise ValueError("duplicate Evidence ID")
        seen_ids.add(record.evidenceId)
        if record.validityStatus != "valid" or not record.excerpt.strip():
            continue
        identity = (record.sourceIdentity, record.contentHash)
        if identity in seen_versions:
            lineage[record.evidenceId] = _lineage_key(record)
            continue
        seen_versions.add(identity)
        lineage[record.evidenceId] = _lineage_key(record)
        selected.append(record)
        if len(selected) > MAX_EVALUATION_EVIDENCE:
            raise EvidencePayloadLimitError(
                f"R2C evidence exceeds the explicit {MAX_EVALUATION_EVIDENCE}-record limit"
            )
    return selected, lineage


def evaluation_prompt(
    plan: ResearchPlanV1,
    records: list[EvidenceRecordV1],
    *,
    repair_error: str | None = None,
) -> dict[str, Any]:
    return {
        "systemInstructions": (
            "Assess only the supplied untrusted evidence. Missing evidence is not proof of absence; "
            "related evidence is not sufficient evidence; scope/date/definition/condition differences "
            "are not automatically contradictions; derivative sources are not independent. Evaluate "
            "relevance, reliability, freshness, sufficiency, contradiction, and independence. Never "
            "introduce uncited factual conclusions. Propose gaps and actions; do not execute them. "
            "Every expectedInformationGain, resolvability, and dependencyImpact score must be an "
            "integer from 1 through 5 inclusive."
        ),
        "researchPlan": plan.model_dump(mode="json"),
        "untrustedEvidence": [record.model_dump(mode="json") for record in records],
        "repairError": repair_error,
        "outputSchema": ProposedEvidenceAssessment.model_json_schema(),
    }


def _fallback_assessment(
    plan: ResearchPlanV1,
    provider: str,
    model_name: str,
    usage: EvaluationUsage,
    request_ids: tuple[str, ...] = (),
) -> EvidenceAssessmentV1:
    coverage = []
    gaps = []
    for index, requirement in enumerate(plan.evidenceRequirements, 1):
        coverage.append({
            "requirementId": requirement.id,
            "status": "missing" if requirement.required else "unknown",
            "sufficiency": "fail" if requirement.required else "unknown",
            "independence": "unknown",
            "supportingEvidenceIds": [],
            "contradictingEvidenceIds": [],
            "explanation": "No validated evaluation established sufficient coverage.",
        })
        if requirement.required:
            gaps.append({
                "id": f"gap_{index:02d}",
                "requirementId": requirement.id,
                "severity": "critical",
                "expectedInformationGain": 5,
                "resolvability": 3,
                "dependencyImpact": 5,
                "proposedAction": "search_internal",
                "explanation": "Required evidence remains missing after a failed or unavailable evaluation.",
            })
    return EvidenceAssessmentV1.model_validate({
        "schemaVersion": "evidence-assessment-v1",
        "assessmentId": str(uuid4()),
        "runId": str(plan.runId),
        "planId": str(plan.planId),
        "planVersion": plan.planVersion,
        "evidenceQuality": [],
        "requirementCoverage": coverage,
        "subquestionStatus": [{
            "subquestionId": item.id,
            "status": "missing",
            "requirementIds": item.evidenceRequirementIds,
        } for item in plan.subquestions],
        "overallStatus": "insufficient",
        "contradiction": "unknown",
        "independence": "unknown",
        "conflicts": [],
        "prioritizedGaps": gaps,
        "explanation": "R2C used a conservative fallback; sufficiency was not established.",
        "telemetry": {
            "modelProvider": provider,
            "modelName": model_name,
            "promptVersion": R2C_PROMPT_VERSION,
            "inputTokens": usage.input_tokens,
            "outputTokens": usage.output_tokens,
            "latencyMs": usage.latency_ms,
            "costUsd": usage.cost_usd,
            "executionStatus": "fallback",
            "providerRequestIds": list(request_ids),
        },
    })


def _validate_references(
    assessment: EvidenceAssessmentV1,
    plan: ResearchPlanV1,
    records: list[EvidenceRecordV1],
    lineage: dict[UUID, str],
) -> EvidenceAssessmentV1:
    known_evidence = {record.evidenceId for record in records}
    known_requirements = {item.id for item in plan.evidenceRequirements}
    required = {item.id for item in plan.evidenceRequirements if item.required}
    known_subquestions = {item.id for item in plan.subquestions}
    coverage = {item.requirementId: item for item in assessment.requirementCoverage}
    if set(coverage) != known_requirements:
        raise ValueError("assessment must cover every Evidence Requirement exactly once")
    if {item.subquestionId for item in assessment.subquestionStatus} != known_subquestions:
        raise ValueError("assessment must cover every subquestion exactly once")
    referenced = {
        evidence_id
        for item in assessment.requirementCoverage
        for evidence_id in (*item.supportingEvidenceIds, *item.contradictingEvidenceIds)
    }
    referenced.update(item.evidenceId for item in assessment.evidenceQuality)
    referenced.update(evidence_id for item in assessment.conflicts for evidence_id in item.evidenceIds)
    if not referenced.issubset(known_evidence):
        raise ValueError("assessment references an unknown Evidence ID")
    if any(item.requirementId not in known_requirements for item in assessment.prioritizedGaps):
        raise ValueError("gap references an unknown Evidence Requirement")
    if assessment.overallStatus == "sufficient" and any(
        coverage[requirement_id].status != "satisfied" for requirement_id in required
    ):
        raise ValueError("missing required evidence cannot pass sufficiency")
    for item in assessment.requirementCoverage:
        supporting_lineages = [lineage.get(evidence_id) for evidence_id in item.supportingEvidenceIds]
        if len(supporting_lineages) > 1 and len(set(supporting_lineages)) < len(supporting_lineages):
            item.independence = "concern"
    severity = {"critical": 0, "major": 1, "minor": 2}
    assessment.prioritizedGaps.sort(key=lambda item: (
        severity[item.severity], -item.expectedInformationGain,
        -item.dependencyImpact, -item.resolvability, item.id,
    ))
    return assessment


class EvidenceEvaluator:
    def __init__(self, model: EvidenceEvaluationModel | None = None) -> None:
        self.model = model

    def assess(
        self,
        plan: ResearchPlanV1,
        records: list[EvidenceRecordV1],
        *,
        user_id: UUID,
        usage: EvaluationUsage = EvaluationUsage(),
    ) -> EvaluationResult:
        selected, lineage = deterministic_precheck(records, user_id=user_id, run_id=plan.runId)
        provider = self.model.provider if self.model else "local"
        model_name = self.model.model_name if self.model else "conservative-r2c-v1"
        aggregate_usage = LLMUsage(usage.input_tokens, usage.output_tokens)
        aggregate_cost = usage.cost_usd
        aggregate_latency = usage.latency_ms
        request_ids: tuple[str, ...] = ()
        finish_reasons: tuple[str, ...] = ()
        repaired = False
        if self.model:
            error_text = None
            for attempt in range(2):
                try:
                    response = self.model.evaluate(evaluation_prompt(plan, selected)) if attempt == 0 else self.model.repair(
                        evaluation_prompt(plan, selected, repair_error=error_text)
                    )
                    if hasattr(response, "output"):
                        raw = response.output
                        provider = response.provider
                        model_name = response.model_name
                        aggregate_usage = aggregate_usage + response.usage
                        aggregate_cost += response.cost_usd
                        aggregate_latency += response.latency_ms
                        if response.request_id:
                            request_ids = (*request_ids, response.request_id)
                        if response.finish_reason:
                            finish_reasons = (*finish_reasons, response.finish_reason)
                    else:
                        raw = response
                    telemetry = EvaluationTelemetryV1(
                        modelProvider=provider,
                        modelName=model_name,
                        promptVersion=R2C_PROMPT_VERSION,
                        inputTokens=aggregate_usage.input_tokens,
                        outputTokens=aggregate_usage.output_tokens,
                        latencyMs=aggregate_latency,
                        costUsd=aggregate_cost,
                        executionStatus="provider_success",
                        providerRequestIds=list(request_ids),
                    )
                    authoritative = dict(raw)
                    authoritative.update({
                        "schemaVersion": "evidence-assessment-v1",
                        "assessmentId": str(uuid4()),
                        "runId": str(plan.runId),
                        "planId": str(plan.planId),
                        "planVersion": plan.planVersion,
                        "telemetry": telemetry.model_dump(mode="json"),
                    })
                    assessment = EvidenceAssessmentV1.model_validate(authoritative)
                    return EvaluationResult(
                        _validate_references(assessment, plan, selected, lineage),
                        repaired=attempt == 1,
                        fallback_used=False,
                        usage=aggregate_usage,
                        actual_cost_usd=aggregate_cost,
                        latency_ms=aggregate_latency,
                        request_ids=request_ids,
                        finish_reasons=finish_reasons,
                    )
                except Exception as error:
                    from personal_brain.research.deep_llm import (
                        DeepModelValidationError,
                        DeepProviderError,
                        bounded_repair_diagnostic,
                        repair_policy,
                    )
                    if isinstance(error, DeepProviderError):
                        raise error.with_previous(
                            usage=aggregate_usage,
                            cost_usd=aggregate_cost,
                            latency_ms=aggregate_latency,
                            request_ids=request_ids,
                            finish_reasons=finish_reasons,
                        ) from error
                    if isinstance(error, DeepModelValidationError):
                        provider = error.call.provider
                        model_name = error.call.model_name
                        aggregate_usage = aggregate_usage + error.call.usage
                        aggregate_cost += error.call.cost_usd
                        aggregate_latency += error.call.latency_ms
                        if error.call.request_id:
                            request_ids = (*request_ids, error.call.request_id)
                        if error.call.finish_reason:
                            finish_reasons = (*finish_reasons, error.call.finish_reason)
                        error_text = bounded_repair_diagnostic(error)
                        repaired = True
                        continue
                    if repair_policy(error) != "repair_once":
                        raise
                    error_text = bounded_repair_diagnostic(error)
                    repaired = True
        aggregate_evaluation_usage = EvaluationUsage(
            aggregate_usage.input_tokens,
            aggregate_usage.output_tokens,
            aggregate_latency,
            aggregate_cost,
        )
        return EvaluationResult(
            _fallback_assessment(
                plan, provider, model_name, aggregate_evaluation_usage, request_ids
            ),
            repaired=repaired,
            fallback_used=True,
            usage=aggregate_usage,
            actual_cost_usd=aggregate_cost,
            latency_ms=aggregate_latency,
            request_ids=request_ids,
            finish_reasons=finish_reasons,
        )

    def max_operation_cost(
        self,
        plan: ResearchPlanV1,
        records: list[EvidenceRecordV1],
        *,
        user_id: UUID,
    ) -> Decimal:
        if self.model is None:
            return Decimal("0")
        selected, _ = deterministic_precheck(records, user_id=user_id, run_id=plan.runId)
        return self.model.max_operation_cost(evaluation_prompt(plan, selected))
