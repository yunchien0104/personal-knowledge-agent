from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from personal_brain.ingestion import LeaseLostError, ProcessResult, _LeaseGuard
from personal_brain.supabase_gateway import ClaimedJob, GatewayError


MINIMUM_SCORE = 60
MAX_NEW_PER_DAY = 10
MAX_AUTOMATIC_COST_USD = Decimal("0.50")
MAX_CANDIDATES = 100


class OpportunityError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AutomaticBudgetExceeded(OpportunityError):
    def __init__(self) -> None:
        super().__init__(
            "automatic_budget_exhausted",
            "Daily automatic processing budget is exhausted",
        )


@dataclass(frozen=True, slots=True)
class RankedOpportunity:
    question: str
    rationale: str
    score: int
    signal_types: tuple[str, ...]
    semantic_key: str
    evidence_ids: tuple[str, ...]
    latest_evidence_at: str
    estimated_cost_usd: Decimal = Decimal("0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "rationale": self.rationale,
            "score": self.score,
            "signalTypes": list(self.signal_types),
            "semanticKey": self.semantic_key,
            "evidenceIds": list(self.evidence_ids),
            "latestEvidenceAt": self.latest_evidence_at,
            "estimatedCostUsd": format(self.estimated_cost_usd, "f"),
        }


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise OpportunityError("invalid_opportunity_context", f"{field} is invalid") from error
    if not parsed.is_finite() or parsed < 0:
        raise OpportunityError("invalid_opportunity_context", f"{field} is invalid")
    return parsed


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise OpportunityError("invalid_opportunity_context", f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise OpportunityError("invalid_opportunity_context", f"{field} is invalid") from error
    if parsed.tzinfo is None:
        raise OpportunityError("invalid_opportunity_context", f"{field} is invalid")
    return parsed.astimezone(timezone.utc)


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _semantic_key(text: str) -> str:
    return sha256(f"claim-gap-v1:{_normalized(text)}".encode()).hexdigest()


def _question(text: str) -> str:
    compact = " ".join(text.split())
    prefix = "What independent evidence supports or contradicts this claim: "
    return f"{prefix}{compact[: 500 - len(prefix)]}"


def _recency_score(observed_at: datetime, now: datetime) -> int:
    age_days = max(0, (now - observed_at).days)
    if age_days <= 7:
        return 30
    if age_days <= 30:
        return 24
    if age_days <= 90:
        return 18
    return 12


def rank_opportunities(
    context: Mapping[str, Any], *, now: datetime | None = None
) -> list[RankedOpportunity]:
    user_id = context.get("userId")
    signals = context.get("signals")
    existing = context.get("existingOpportunities", [])
    completed_questions = context.get("completedQuestions", [])
    if not isinstance(user_id, str) or not isinstance(signals, list):
        raise OpportunityError("invalid_opportunity_context", "Opportunity context is invalid")
    if not isinstance(existing, list) or not isinstance(completed_questions, list):
        raise OpportunityError("invalid_opportunity_context", "Opportunity context is invalid")

    spent = _decimal(context.get("automaticSpentUsd", "0"), "automaticSpentUsd")
    estimated = _decimal(context.get("estimatedRunCostUsd", "0"), "estimatedRunCostUsd")
    if spent + estimated > MAX_AUTOMATIC_COST_USD:
        raise AutomaticBudgetExceeded()

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    completed = {_normalized(value) for value in completed_questions if isinstance(value, str)}
    existing_by_key: dict[str, datetime | None] = {}
    for row in existing:
        if not isinstance(row, dict) or not isinstance(row.get("semanticKey"), str):
            raise OpportunityError("invalid_opportunity_context", "Existing opportunity is invalid")
        cooldown = row.get("cooldownUntil")
        existing_by_key[row["semanticKey"]] = None if cooldown is None else _timestamp(
            cooldown, "cooldownUntil"
        )

    candidates: dict[str, RankedOpportunity] = {}
    for signal in signals[:MAX_CANDIDATES]:
        if not isinstance(signal, dict) or signal.get("userId") != user_id:
            raise OpportunityError("tenant_mismatch", "Opportunity signal tenant is invalid")
        text = signal.get("text")
        signal_type = signal.get("signalType")
        evidence_ids = signal.get("evidenceIds")
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(signal_type, str)
            or not isinstance(evidence_ids, list)
            or not all(isinstance(value, str) for value in evidence_ids)
        ):
            raise OpportunityError("invalid_opportunity_context", "Opportunity signal is invalid")
        semantic_key = _semantic_key(text)
        cooldown_until = existing_by_key.get(semantic_key)
        if cooldown_until is not None and cooldown_until > current:
            continue
        observed_at = _timestamp(signal.get("observedAt"), "observedAt")
        question = _question(text)
        novelty = 0 if _normalized(question) in completed else 25
        verification = signal.get("verificationStatus")
        needs_review = signal.get("needsReview") is True
        gap = 20 if needs_review or verification in {
            "insufficient_evidence", "partially_supported", "disputed", "outdated"
        } else 15
        researchability = 15 if evidence_ids else 0
        value = 10 if signal.get("claimType") == "source_assertion" else 8
        score = _recency_score(observed_at, current) + novelty + gap + researchability + value
        if score < MINIMUM_SCORE:
            continue
        candidate = RankedOpportunity(
            question=question,
            rationale=(
                "A recent claim still needs independent evidence or contradiction checking."
            ),
            score=min(score, 100),
            signal_types=(signal_type,),
            semantic_key=semantic_key,
            evidence_ids=tuple(dict.fromkeys(evidence_ids))[:20],
            latest_evidence_at=observed_at.isoformat().replace("+00:00", "Z"),
            estimated_cost_usd=estimated,
        )
        previous = candidates.get(semantic_key)
        if previous is None or (candidate.score, candidate.latest_evidence_at) > (
            previous.score, previous.latest_evidence_at
        ):
            candidates[semantic_key] = candidate

    ranked = sorted(
        candidates.values(),
        key=lambda item: (-item.score, item.semantic_key),
    )
    selected: list[RankedOpportunity] = []
    new_count = 0
    for candidate in ranked:
        is_new = candidate.semantic_key not in existing_by_key
        if is_new and new_count >= MAX_NEW_PER_DAY:
            continue
        selected.append(candidate)
        new_count += int(is_new)
    return selected


class OpportunityWorker:
    def __init__(self, gateway: Any, worker_id: str, *, lease_seconds: int = 120) -> None:
        self.gateway = gateway
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self._scheduled_date: str | None = None

    def process_one(self) -> ProcessResult:
        ranking_date = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
        if self._scheduled_date != ranking_date:
            self.gateway.enqueue_due_opportunities()
            self._scheduled_date = ranking_date
        job: ClaimedJob | None = self.gateway.claim(
            self.worker_id, self.lease_seconds, "opportunity"
        )
        if job is None:
            return ProcessResult("idle")
        try:
            if job.payload.job_type != "rank_opportunities":
                raise OpportunityError("invalid_claim", "Worker received an unsupported Job")
            with _LeaseGuard(self.gateway, job, self.worker_id, self.lease_seconds) as lease:
                context = self.gateway.opportunity_context(job, self.worker_id)
                if context is None:
                    raise LeaseLostError("Worker lease is no longer valid")
                candidates = rank_opportunities(context)
                lease.ensure()
            result = self.gateway.publish_opportunities(
                job,
                self.worker_id,
                candidates=[candidate.to_dict() for candidate in candidates],
                estimated_cost_usd="0",
            )
            if result is None:
                raise LeaseLostError("Worker lease expired before publication")
            if result.get("status") == "paused_budget":
                return ProcessResult("paused_budget", job.id)
            return ProcessResult("completed", job.id)
        except AutomaticBudgetExceeded:
            self.gateway.pause_opportunity_budget(job, self.worker_id)
            return ProcessResult("paused_budget", job.id)
        except LeaseLostError:
            return ProcessResult("lease_lost", job.id)
        except (OpportunityError, GatewayError) as error:
            self.gateway.report_failure(
                job,
                self.worker_id,
                error_class="transient" if error.retryable else "permanent",
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
            )
            return ProcessResult("retry_wait" if error.retryable else "failed", job.id)
