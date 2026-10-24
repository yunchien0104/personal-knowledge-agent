
from __future__ import annotations

from dataclasses import dataclass

from personal_brain.research.contracts import VerificationResult
from personal_brain.research.contracts import AtomicResearchDraft

@dataclass(frozen=True)
class RepairDecision:
    should_repair: bool
    claim_ids: list[str]
    reason: str


def evaluate_repair(
    results: list[VerificationResult],
    attempts_used: int,
    max_attempts: int = 1,
) -> RepairDecision:
    """Decide whether a research draft may be repaired."""

    if attempts_used < 0 or max_attempts < 0:
        raise ValueError("Repair attempts cannot be negative")

    # 遇到矛盾證據，先停止自動修復
    if any(r.verdict == "contradicted" for r in results):
        return RepairDecision(
            should_repair=False,
            claim_ids=[],
            reason="contradicted_claim",
        )

    # 所有 Claim 都通過，不需要修復
    if all(r.verdict == "supported" for r in results):
        return RepairDecision(
            should_repair=False,
            claim_ids=[],
            reason="all_supported",
        )

    # 已達修復次數上限
    if attempts_used >= max_attempts:
        return RepairDecision(
            should_repair=False,
            claim_ids=[],
            reason="attempt_limit_reached",
        )

    repairable = [
        r.claimId
        for r in results
        if r.verdict in ("unsupported", "insufficient_evidence")
    ]

    return RepairDecision(
        should_repair=bool(repairable),
        claim_ids=repairable,
        reason="repairable_claims" if repairable else "no_repairable_claims",
    )


def validate_repaired_draft(
    original: AtomicResearchDraft,
    repaired: AtomicResearchDraft,
    results: list[VerificationResult],
) -> None:
    """Reject unauthorized changes to research claims."""

    def claim_map(draft):
        claims = [
            claim
            for finding in draft.findings
            for claim in finding.claims
        ]

        mapping = {claim.claimId: claim for claim in claims}

        if len(mapping) != len(claims):
            raise ValueError("Duplicate claim IDs")

        return mapping

    original_claims = claim_map(original)
    repaired_claims = claim_map(repaired)

    # 每個原始 Claim 都必須有驗證結果
    verdicts = {r.claimId: r.verdict for r in results}

    if len(verdicts) != len(results):
        raise ValueError("Duplicate verification feedback")

    if set(verdicts) != set(original_claims):
        raise ValueError("Incomplete verification feedback")

    # 禁止新增 Claim ID
    if not set(repaired_claims).issubset(original_claims):
        raise ValueError("Repair introduced new claim IDs")

    # 已通過的 Claim 不可以修改，也不可以刪除
    for claim_id, original_claim in original_claims.items():
        if verdicts[claim_id] == "supported":
            if repaired_claims.get(claim_id) != original_claim:
                raise ValueError(
                    f"Supported claim was changed: {claim_id}"
                )
