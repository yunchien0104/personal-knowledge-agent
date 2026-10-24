
from personal_brain.research.contracts import VerificationResult
from personal_brain.research.repair import evaluate_repair


def make_result(claim_id, verdict):
    return VerificationResult(
        claimId=claim_id,
        verdict=verdict,
        explanation="Test verdict",
    )


# Case 1: 需要修復
decision = evaluate_repair(
    [
        make_result("C001", "supported"),
        make_result("C002", "insufficient_evidence"),
    ],
    attempts_used=0,
)

assert decision.should_repair is True
assert decision.claim_ids == ["C002"]
print("PASS: Repairable claim detected")


# Case 2: 已達上限
decision = evaluate_repair(
    [make_result("C002", "unsupported")],
    attempts_used=1,
)

assert decision.should_repair is False
assert decision.reason == "attempt_limit_reached"
print("PASS: Repair attempt limit enforced")


# Case 3: 矛盾證據
decision = evaluate_repair(
    [make_result("C003", "contradicted")],
    attempts_used=0,
)

assert decision.should_repair is False
assert decision.reason == "contradicted_claim"
print("PASS: Contradicted claim blocks automatic repair")


# Case 4: 全部通過
decision = evaluate_repair(
    [
        make_result("C001", "supported"),
        make_result("C002", "supported"),
    ],
    attempts_used=0,
)

assert decision.should_repair is False
assert decision.reason == "all_supported"
print("PASS: Fully verified draft does not need repair")
