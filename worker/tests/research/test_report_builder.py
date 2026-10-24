
from personal_brain.research.contracts import AtomicResearchDraft
from personal_brain.research.gate import GateDecision
from personal_brain.research.report_builder import build_verified_report


draft = AtomicResearchDraft.model_validate({
    "schemaVersion": "atomic-research-v1",
    "title": "A 公司研究",
    "summary": "公司即將破產。",  # 故意放入未驗證內容
    "findings": [{
        "findingId": "F001",
        "topic": "公司即將破產",  # 故意放入未驗證內容
        "claims": [{
            "claimId": "C001",
            "statement": "2025 年營收成長率為 20%。",
            "claimType": "fact",
            "citations": [{
                "evidenceId": "E001",
                "quote": "2025 年營收成長率為 20%。",
                "relationType": "supports",
            }],
        }],
    }],
})

gate = GateDecision(
    approved_claim_ids=["C001"],
    rejected_claim_ids=[],
    repair_claim_ids=[],
    can_publish=True,
)

report = build_verified_report(draft, gate)

assert "2025 年營收成長率為 20%" in report
assert "公司即將破產" not in report
assert "[Evidence E001]" in report

print("PASS: Only approved claims appear in report")


blocked_gate = GateDecision(
    approved_claim_ids=[],
    rejected_claim_ids=["C001"],
    repair_claim_ids=[],
    can_publish=False,
)

try:
    build_verified_report(draft, blocked_gate)
except ValueError:
    print("PASS: Blocked draft cannot be published")
else:
    raise AssertionError("Blocked draft was published")
