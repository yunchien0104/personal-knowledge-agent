"""Pure deterministic R2D decision logic.

Durable execution and side effects are deliberately outside this module; the
controller only selects an allowed next action or a truthful stop outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from uuid import uuid4

from personal_brain.research.deep_contracts import (
    EvidenceAssessmentV1,
    ResearchActionV1,
    ResearchIterationStateV1,
    ResearchPlanV1,
    ResearchRequestV1,
)


@dataclass(frozen=True, slots=True)
class ControllerDecision:
    action: ResearchActionV1
    stop_reason: str | None = None
    outcome: str | None = None


def query_fingerprint(query: str) -> str:
    normalized = re.sub(r"\s+", " ", query.casefold()).strip()
    normalized = re.sub(r"[^\w\s]", "", normalized)
    return sha256(normalized.encode()).hexdigest()


def material_coverage_gain(
    before: dict[str, str], after: dict[str, str]
) -> bool:
    value = {"unknown": 0, "missing": 0, "partial": 1, "conflicted": 1, "satisfied": 2}
    return any(value.get(after.get(key, "unknown"), 0) > value.get(status, 0) for key, status in before.items())


def _stop(reason: str, outcome: str) -> ControllerDecision:
    return ControllerDecision(
        ResearchActionV1(
            actionId=uuid4(), actionType="stop_retrieval", reason=reason, query=None, gapIds=[]
        ),
        stop_reason=reason,
        outcome=outcome,
    )


class ResearchIterationController:
    def decide(
        self,
        request: ResearchRequestV1,
        plan: ResearchPlanV1,
        assessment: EvidenceAssessmentV1,
        state: ResearchIterationStateV1,
    ) -> ControllerDecision:
        has_material_contradiction = any(
            item.classification == "contradiction" for item in assessment.conflicts
        )
        has_critical_gap = any(
            item.severity == "critical" for item in assessment.prioritizedGaps
        )
        coverage = {
            item.requirementId: item for item in assessment.requirementCoverage
        }
        required_ids = {
            item.id for item in plan.evidenceRequirements if item.required
        }
        required_coverage_complete = bool(required_ids) and all(
            requirement_id in coverage
            and coverage[requirement_id].status == "satisfied"
            and coverage[requirement_id].sufficiency == "pass"
            for requirement_id in required_ids
        )
        if (
            assessment.overallStatus == "sufficient"
            and required_coverage_complete
            and not has_material_contradiction
            and not has_critical_gap
        ):
            return _stop("quality_reached", "complete")
        if state.currentRound >= request.effectiveLimits.maxRounds:
            return _stop("max_rounds_reached", "qualified_partial")
        if state.consecutiveNoInformationGainRounds >= 2:
            return _stop("diminishing_returns", "qualified_partial")
        spend = state.budget.reservedUsd + state.budget.actualUsd + state.budget.uncertainUsd
        available = state.budget.limitUsd - spend - state.budget.protectedFinalReportReserveUsd
        if available <= 0:
            return _stop("budget_exhausted", "qualified_partial")

        used = set(state.queryFingerprints)
        critical_gaps = [item for item in assessment.prioritizedGaps if item.severity == "critical"]
        gaps = critical_gaps or assessment.prioritizedGaps
        gap_ids = [item.id for item in gaps[:3]]
        proposals = sorted(
            plan.retrievalProposals,
            key=lambda item: (item.source != "internal", item.id),
        )
        for proposal in proposals:
            fingerprint = query_fingerprint(proposal.query)
            if fingerprint in used:
                continue
            if proposal.source == "internal":
                return ControllerDecision(ResearchActionV1(
                    actionId=uuid4(), actionType="search_internal",
                    reason="Address the highest-priority unresolved evidence gap internally first",
                    query=proposal.query, gapIds=gap_ids,
                ))
            if request.webPolicy == "auto" and request.permissions.webDiscovery:
                return ControllerDecision(ResearchActionV1(
                    actionId=uuid4(), actionType="discover_web",
                    reason="No unused internal proposal remains and authorized Web discovery may improve coverage",
                    query=proposal.query, gapIds=gap_ids,
                ))
        if request.webPolicy == "suggest" and assessment.prioritizedGaps:
            return _stop("authorization_blocked", "qualified_partial")
        if request.webPolicy == "disabled" or not request.permissions.webDiscovery:
            return _stop("no_permitted_action", "qualified_partial")
        return _stop("diminishing_returns", "qualified_partial")
