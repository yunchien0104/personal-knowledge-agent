"""Strict, versioned contracts for Deep Research (R2B/R2C/R2D).

These contracts intentionally do not extend the legacy Research plan.  The
legacy worker and SQL validators accept a different, single-round schema.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


_ID_PATTERN = r"^[a-z][a-z0-9_-]{1,63}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

AssumptionConfidenceV1 = Literal["low", "medium", "high"]
EvidenceTypeV1 = Literal[
    "fact", "comparison", "trend", "counter_evidence", "primary_source", "other"
]
AssessmentOverallStatusV1 = Literal[
    "sufficient", "partial", "insufficient", "conflicted"
]


class DeepStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AnalysisSpecV1(DeepStrictModel):
    schemaVersion: Literal["analysis-spec-v1"]
    framework: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)


class InternalKnowledgeScopeV1(DeepStrictModel):
    selectedAssetIds: list[UUID] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_assets(self) -> InternalKnowledgeScopeV1:
        if self.selectedAssetIds and len(set(self.selectedAssetIds)) != len(self.selectedAssetIds):
            raise ValueError("selectedAssetIds must be unique")
        return self


class ResearchPermissionsV1(DeepStrictModel):
    internalKnowledge: bool = True
    webDiscovery: bool = False
    webFetch: bool = False

    @model_validator(mode="after")
    def discovery_precedes_fetch(self) -> ResearchPermissionsV1:
        if self.webFetch and not self.webDiscovery:
            raise ValueError("webFetch requires webDiscovery")
        return self


class ResearchLimitsV1(DeepStrictModel):
    maxRounds: int = Field(ge=1, le=10)
    maxQueries: int = Field(ge=1, le=50)
    maxCostUsd: Decimal = Field(ge=0, max_digits=8, decimal_places=4)
    maxDurationSeconds: int = Field(ge=30, le=14_400)


class ResearchRequestV1(DeepStrictModel):
    schemaVersion: Literal["research-request-v1"]
    requestId: UUID
    runId: UUID
    idempotencyKey: UUID
    userId: UUID
    objective: str = Field(min_length=3, max_length=1000)
    originalQuestion: str = Field(min_length=3, max_length=500)
    executionMode: Literal["deep"]
    internalKnowledgeScope: InternalKnowledgeScopeV1
    webPolicy: Literal["disabled", "suggest", "auto"]
    permissions: ResearchPermissionsV1
    effectiveLimits: ResearchLimitsV1
    analysisSpec: AnalysisSpecV1 | None = None

    @model_validator(mode="after")
    def policy_is_authorized(self) -> ResearchRequestV1:
        if self.webPolicy == "auto" and not (
            self.permissions.webDiscovery and self.permissions.webFetch
        ):
            raise ValueError("auto web policy requires discovery and fetch permission")
        if self.webPolicy == "disabled" and (
            self.permissions.webDiscovery or self.permissions.webFetch
        ):
            raise ValueError("disabled web policy cannot grant web permissions")
        return self


class ResearchConstraintV1(DeepStrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    description: str = Field(min_length=1, max_length=1000)


class ResearchAssumptionV1(DeepStrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    description: str = Field(min_length=1, max_length=1000)
    confidence: AssumptionConfidenceV1


class EvidenceRequirementV1(DeepStrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    subquestionId: str = Field(pattern=_ID_PATTERN)
    description: str = Field(min_length=1, max_length=1000)
    required: bool
    evidenceType: EvidenceTypeV1


class ResearchSubquestionV1(DeepStrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    question: str = Field(min_length=3, max_length=500)
    priority: int = Field(ge=1, le=5)
    dependsOn: list[str] = Field(default_factory=list, max_length=8)
    evidenceRequirementIds: list[str] = Field(min_length=1, max_length=5)


class CoverageCriterionV1(DeepStrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    description: str = Field(min_length=1, max_length=1000)
    evidenceRequirementIds: list[str] = Field(min_length=1, max_length=40)


class RetrievalProposalV1(DeepStrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    subquestionId: str = Field(pattern=_ID_PATTERN)
    source: Literal["internal", "web"]
    query: str = Field(min_length=2, max_length=500)
    purpose: str = Field(min_length=1, max_length=500)


def _near_duplicate(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold())


class ResearchPlanV1(DeepStrictModel):
    schemaVersion: Literal["research-plan-v1"]
    planId: UUID
    runId: UUID
    requestId: UUID
    planVersion: int = Field(ge=1, le=100)
    normalizedResearchIntent: str = Field(min_length=3, max_length=1000)
    objective: str = Field(min_length=3, max_length=1000)
    explicitConstraints: list[ResearchConstraintV1] = Field(default_factory=list, max_length=20)
    inferredAssumptions: list[ResearchAssumptionV1] = Field(default_factory=list, max_length=20)
    essentialClarifications: list[str] = Field(default_factory=list, max_length=5)
    subquestions: list[ResearchSubquestionV1] = Field(min_length=1, max_length=8)
    evidenceRequirements: list[EvidenceRequirementV1] = Field(min_length=1, max_length=40)
    coverageCriteria: list[CoverageCriterionV1] = Field(min_length=1, max_length=20)
    retrievalProposals: list[RetrievalProposalV1] = Field(min_length=1, max_length=32)
    revisionParentPlanId: UUID | None = None
    revisionReason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_graph_and_references(self) -> ResearchPlanV1:
        subquestion_ids = [item.id for item in self.subquestions]
        requirement_ids = [item.id for item in self.evidenceRequirements]
        all_ids = [
            *(item.id for item in self.explicitConstraints),
            *(item.id for item in self.inferredAssumptions),
            *subquestion_ids,
            *requirement_ids,
            *(item.id for item in self.coverageCriteria),
            *(item.id for item in self.retrievalProposals),
        ]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("plan IDs must be globally unique")
        known_subquestions = set(subquestion_ids)
        known_requirements = set(requirement_ids)
        normalized = [_near_duplicate(item.question) for item in self.subquestions]
        if len(normalized) != len(set(normalized)):
            raise ValueError("subquestions must not be duplicates")
        requirement_owner = {item.id: item.subquestionId for item in self.evidenceRequirements}
        for requirement in self.evidenceRequirements:
            if requirement.subquestionId not in known_subquestions:
                raise ValueError("Evidence Requirement references an unknown subquestion")
        for subquestion in self.subquestions:
            if subquestion.id in subquestion.dependsOn:
                raise ValueError("subquestion cannot depend on itself")
            if not set(subquestion.dependsOn).issubset(known_subquestions):
                raise ValueError("dependsOn references an unknown subquestion")
            if not set(subquestion.evidenceRequirementIds).issubset(known_requirements):
                raise ValueError("subquestion references an unknown Evidence Requirement")
            if any(
                requirement_owner[requirement_id] != subquestion.id
                for requirement_id in subquestion.evidenceRequirementIds
            ):
                raise ValueError("Evidence Requirement belongs to another subquestion")
        graph = {item.id: item.dependsOn for item in self.subquestions}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("subquestion dependency graph contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)
        for criterion in self.coverageCriteria:
            if not set(criterion.evidenceRequirementIds).issubset(known_requirements):
                raise ValueError("Coverage Criterion references an unknown requirement")
        proposal_counts: dict[str, int] = {}
        for proposal in self.retrievalProposals:
            if proposal.subquestionId not in known_subquestions:
                raise ValueError("retrieval proposal references an unknown subquestion")
            proposal_counts[proposal.subquestionId] = proposal_counts.get(proposal.subquestionId, 0) + 1
            if proposal_counts[proposal.subquestionId] > 4:
                raise ValueError("a subquestion can have at most four initial retrieval proposals")
        if (self.revisionParentPlanId is None) != (self.revisionReason is None):
            raise ValueError("revision parent and reason must be provided together")
        return self

    def validate_against_request(self, request: ResearchRequestV1) -> ResearchPlanV1:
        if self.runId != request.runId or self.requestId != request.requestId:
            raise ValueError("plan identity does not match request")
        if _near_duplicate(self.objective) != _near_duplicate(request.objective):
            raise ValueError("plan objective must preserve the authoritative request objective")
        if any(item.source == "web" for item in self.retrievalProposals):
            if request.webPolicy == "disabled" or not request.permissions.webDiscovery:
                raise ValueError("plan proposes unauthorized web retrieval")
        if len(self.retrievalProposals) > request.effectiveLimits.maxQueries:
            raise ValueError("plan exceeds the effective query limit")
        return self


class EvidenceProvenanceV1(DeepStrictModel):
    assetId: UUID | None = None
    revisionId: UUID | None = None
    parentEvidenceIds: list[UUID] = Field(default_factory=list, max_length=20)
    captureMethod: str = Field(min_length=1, max_length=100)


class EvidenceRecordV1(DeepStrictModel):
    schemaVersion: Literal["evidence-record-v1"]
    evidenceId: UUID
    runId: UUID
    userId: UUID
    origin: Literal["internal", "web"]
    sourceIdentity: str = Field(min_length=1, max_length=1000)
    sourceMetadata: dict[str, Any]
    provenance: EvidenceProvenanceV1
    excerpt: str = Field(min_length=1, max_length=12_000)
    citationLocator: dict[str, Any]
    contentHash: str = Field(pattern=_SHA256_PATTERN)
    capturedAt: datetime
    sourcePublishedAt: datetime | None = None
    storageScope: Literal["research_run", "knowledge_base"]
    citationEligible: bool
    validityStatus: Literal["valid", "invalid", "source_deleted", "superseded"]
    isSearchSnippet: bool = False

    @model_validator(mode="after")
    def validate_origin(self) -> EvidenceRecordV1:
        if self.origin == "internal" and (
            self.provenance.assetId is None or self.provenance.revisionId is None
        ):
            raise ValueError("internal evidence requires Asset and Revision provenance")
        if self.origin == "web" and self.storageScope != "research_run":
            raise ValueError("web evidence is run-scoped until explicitly imported")
        if self.isSearchSnippet and self.citationEligible:
            raise ValueError("search snippets are never citation-eligible")
        if self.citationEligible and (
            self.validityStatus != "valid" or not self.citationLocator
        ):
            raise ValueError("citation eligibility requires valid source content and a locator")
        return self


EvaluationLevel = Literal["pass", "concern", "fail", "unknown"]


class EvidenceQualityV1(DeepStrictModel):
    evidenceId: UUID
    relevance: EvaluationLevel
    reliability: EvaluationLevel
    freshness: EvaluationLevel
    explanation: str = Field(min_length=1, max_length=1000)


class RequirementCoverageV1(DeepStrictModel):
    requirementId: str = Field(pattern=_ID_PATTERN)
    status: Literal["satisfied", "partial", "missing", "conflicted", "unknown"]
    sufficiency: EvaluationLevel
    independence: EvaluationLevel
    supportingEvidenceIds: list[UUID] = Field(default_factory=list, max_length=100)
    contradictingEvidenceIds: list[UUID] = Field(default_factory=list, max_length=100)
    explanation: str = Field(min_length=1, max_length=2000)


class EvidenceConflictV1(DeepStrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    classification: Literal["contradiction", "scope_difference", "definition_difference", "date_difference", "condition_difference"]
    evidenceIds: list[UUID] = Field(min_length=2, max_length=20)
    explanation: str = Field(min_length=1, max_length=2000)


class ResearchGapV1(DeepStrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    requirementId: str = Field(pattern=_ID_PATTERN)
    severity: Literal["critical", "major", "minor"]
    expectedInformationGain: int = Field(ge=1, le=5)
    resolvability: int = Field(ge=1, le=5)
    dependencyImpact: int = Field(ge=1, le=5)
    proposedAction: Literal["search_internal", "revise_query", "discover_web", "fetch_web", "revise_plan"]
    explanation: str = Field(min_length=1, max_length=2000)


class SubquestionAssessmentV1(DeepStrictModel):
    subquestionId: str = Field(pattern=_ID_PATTERN)
    status: Literal["satisfied", "partial", "missing", "conflicted", "unknown"]
    requirementIds: list[str] = Field(min_length=1, max_length=5)


class EvaluationTelemetryV1(DeepStrictModel):
    modelProvider: str = Field(min_length=1, max_length=64)
    modelName: str = Field(min_length=1, max_length=128)
    promptVersion: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    inputTokens: int = Field(ge=0)
    outputTokens: int = Field(ge=0)
    latencyMs: int = Field(ge=0)
    costUsd: Decimal = Field(ge=0, max_digits=8, decimal_places=4)
    executionStatus: Literal["provider_success", "fallback"] = "provider_success"
    providerRequestIds: list[str] = Field(default_factory=list, max_length=2)


class EvidenceAssessmentV1(DeepStrictModel):
    schemaVersion: Literal["evidence-assessment-v1"]
    assessmentId: UUID
    runId: UUID
    planId: UUID
    planVersion: int = Field(ge=1)
    evidenceQuality: list[EvidenceQualityV1] = Field(default_factory=list, max_length=200)
    requirementCoverage: list[RequirementCoverageV1] = Field(min_length=1, max_length=40)
    subquestionStatus: list[SubquestionAssessmentV1] = Field(min_length=1, max_length=8)
    overallStatus: AssessmentOverallStatusV1
    contradiction: EvaluationLevel
    independence: EvaluationLevel
    conflicts: list[EvidenceConflictV1] = Field(default_factory=list, max_length=50)
    prioritizedGaps: list[ResearchGapV1] = Field(default_factory=list, max_length=40)
    explanation: str = Field(min_length=1, max_length=4000)
    telemetry: EvaluationTelemetryV1

    @model_validator(mode="after")
    def required_coverage_controls_status(self) -> EvidenceAssessmentV1:
        coverage_ids = [item.requirementId for item in self.requirementCoverage]
        if len(coverage_ids) != len(set(coverage_ids)):
            raise ValueError("Requirement Coverage must be unique")
        if self.overallStatus == "sufficient" and any(
            item.status != "satisfied" or item.sufficiency != "pass"
            for item in self.requirementCoverage
        ):
            raise ValueError(
                "overall sufficient requires every assessed requirement to be satisfied and sufficient"
            )
        if self.overallStatus == "sufficient" and any(
            item.severity == "critical" for item in self.prioritizedGaps
        ):
            raise ValueError("overall sufficient cannot retain a critical research gap")
        evidence_ids = [item.evidenceId for item in self.evidenceQuality]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence Quality entries must be unique")
        return self


class ResearchActionV1(DeepStrictModel):
    actionId: UUID
    actionType: Literal[
        "search_internal", "revise_query", "discover_web", "fetch_web", "revise_plan", "stop_retrieval"
    ]
    reason: str = Field(min_length=1, max_length=2000)
    query: str | None = Field(default=None, min_length=2, max_length=500)
    gapIds: list[str] = Field(default_factory=list, max_length=40)


class ResearchBudgetStateV1(DeepStrictModel):
    limitUsd: Decimal = Field(ge=0, max_digits=8, decimal_places=4)
    protectedFinalReportReserveUsd: Decimal = Field(ge=0, max_digits=8, decimal_places=4)
    reservedUsd: Decimal = Field(ge=0, max_digits=8, decimal_places=4)
    actualUsd: Decimal = Field(ge=0, max_digits=8, decimal_places=4)
    uncertainUsd: Decimal = Field(ge=0, max_digits=8, decimal_places=4)

    @model_validator(mode="after")
    def within_limit(self) -> ResearchBudgetStateV1:
        if self.reservedUsd + self.actualUsd + self.uncertainUsd > self.limitUsd:
            raise ValueError("budget state exceeds the run limit")
        return self


class ResearchIterationStateV1(DeepStrictModel):
    schemaVersion: Literal["research-iteration-state-v1"]
    runId: UUID
    planVersion: int = Field(ge=1)
    checkpointVersion: int = Field(ge=1)
    currentRound: int = Field(ge=0)
    completedRounds: list[int] = Field(default_factory=list, max_length=10)
    executionPhase: Literal[
        "planning", "retrieving", "assessing", "deciding", "synthesizing", "verifying", "publishing", "terminal"
    ]
    queryFingerprints: list[str] = Field(default_factory=list, max_length=100)
    operationIds: list[UUID] = Field(default_factory=list, max_length=200)
    assessmentIds: list[UUID] = Field(default_factory=list, max_length=20)
    requirementProgress: dict[str, Literal["satisfied", "partial", "missing", "conflicted", "unknown"]]
    unresolvedCriticalGapIds: list[str] = Field(default_factory=list, max_length=40)
    proposedActions: list[ResearchActionV1] = Field(default_factory=list, max_length=20)
    approvedActionIds: list[UUID] = Field(default_factory=list, max_length=20)
    budget: ResearchBudgetStateV1
    consecutiveNoInformationGainRounds: int = Field(ge=0, le=10)
    stopReason: Literal[
        "quality_reached", "diminishing_returns", "budget_exhausted", "max_rounds_reached",
        "max_queries_exhausted", "duration_exhausted", "cancelled",
        "authorization_blocked", "lease_lost", "no_permitted_action"
    ] | None = None
    outcome: Literal["complete", "qualified_partial", "insufficient", "cancelled", "failed"] | None = None

    @model_validator(mode="after")
    def terminal_fields_are_consistent(self) -> ResearchIterationStateV1:
        if self.executionPhase == "terminal" and (self.stopReason is None or self.outcome is None):
            raise ValueError("terminal state requires stopReason and outcome")
        if self.executionPhase != "terminal" and (self.stopReason is not None or self.outcome is not None):
            raise ValueError("non-terminal state cannot have stopReason or outcome")
        if len(self.queryFingerprints) != len(set(self.queryFingerprints)):
            raise ValueError("query history must not contain duplicate fingerprints")
        if not set(self.approvedActionIds).issubset({item.actionId for item in self.proposedActions}):
            raise ValueError("approved actions must reference proposed actions")
        return self


VALID_PHASE_TRANSITIONS: dict[str, frozenset[str]] = {
    "planning": frozenset({"retrieving", "terminal"}),
    "retrieving": frozenset({"assessing", "terminal"}),
    "assessing": frozenset({"deciding", "terminal"}),
    "deciding": frozenset({"planning", "retrieving", "synthesizing", "terminal"}),
    "synthesizing": frozenset({"verifying", "terminal"}),
    "verifying": frozenset({"publishing", "terminal"}),
    "publishing": frozenset({"terminal"}),
    "terminal": frozenset(),
}


def validate_iteration_transition(
    previous: ResearchIterationStateV1, next_state: ResearchIterationStateV1
) -> ResearchIterationStateV1:
    if previous.runId != next_state.runId:
        raise ValueError("iteration state cannot change Research Run identity")
    if next_state.checkpointVersion != previous.checkpointVersion + 1:
        raise ValueError("checkpoint version must advance exactly once")
    if next_state.executionPhase not in VALID_PHASE_TRANSITIONS[previous.executionPhase]:
        raise ValueError("invalid research execution phase transition")
    if next_state.planVersion < previous.planVersion:
        raise ValueError("plan version cannot move backwards")
    if next_state.currentRound < previous.currentRound:
        raise ValueError("research round cannot move backwards")
    return next_state
