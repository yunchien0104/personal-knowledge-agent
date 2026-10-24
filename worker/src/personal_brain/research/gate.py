
from __future__ import annotations

from dataclasses import dataclass

from personal_brain.research.contracts import VerificationResult


@dataclass(frozen=True)
class GateDecision:
    approved_claim_ids: list[str]
    rejected_claim_ids: list[str]
    repair_claim_ids: list[str]
    can_publish: bool


def evaluate_gate(
    results: list[VerificationResult],
    expected_claim_ids: list[str],
) -> GateDecision:
    """Decide which claims may be published."""

    expected = set(expected_claim_ids)

    # 防止重複 Claim ID
    if len(expected) != len(expected_claim_ids):
        raise ValueError("Duplicate expected claim IDs")

    # 確保每個 Claim 都有且只有一個驗證結果
    actual_ids = [result.claimId for result in results]

    if len(actual_ids) != len(set(actual_ids)):
        raise ValueError("Duplicate verification results")

    if set(actual_ids) != expected:
        raise ValueError("Verification results do not match claims")

    approved = []
    rejected = []
    repair = []

    for result in results:
        if result.verdict == "supported":
            approved.append(result.claimId)

        elif result.verdict == "contradicted":
            rejected.append(result.claimId)

        elif result.verdict in (
            "unsupported",
            "insufficient_evidence",
        ):
            repair.append(result.claimId)

    # 第一版採取保守政策：
    # 所有 Claim 通過才允許發布
    can_publish = bool(expected) and len(approved) == len(expected)

    return GateDecision(
        approved_claim_ids=approved,
        rejected_claim_ids=rejected,
        repair_claim_ids=repair,
        can_publish=can_publish,
    )
