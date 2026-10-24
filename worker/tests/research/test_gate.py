
from personal_brain.research.contracts import VerificationResult
from personal_brain.research.gate import evaluate_gate


def result(claim_id, verdict):
    return VerificationResult(
        claimId=claim_id,
        verdict=verdict,
        explanation="Test result",
    )


# Case 1：全部通過
decision = evaluate_gate(
    [
        result("C001", "supported"),
        result("C002", "supported"),
    ],
    ["C001", "C002"],
)

assert decision.can_publish is True
assert decision.approved_claim_ids == ["C001", "C002"]
print("PASS: All supported")


# Case 2：有一個證據不足
decision = evaluate_gate(
    [
        result("C001", "supported"),
        result("C002", "insufficient_evidence"),
    ],
    ["C001", "C002"],
)

assert decision.can_publish is False
assert decision.repair_claim_ids == ["C002"]
print("PASS: Insufficient evidence blocks publication")


# Case 3：有一個被證據反駁
decision = evaluate_gate(
    [
        result("C001", "supported"),
        result("C002", "contradicted"),
    ],
    ["C001", "C002"],
)

assert decision.can_publish is False
assert decision.rejected_claim_ids == ["C002"]
print("PASS: Contradicted claim blocks publication")


# Case 4：遺漏驗證結果
try:
    evaluate_gate(
        [result("C001", "supported")],
        ["C001", "C002"],
    )
except ValueError:
    print("PASS: Missing verification rejected")
else:
    raise AssertionError("Missing verification was accepted")
