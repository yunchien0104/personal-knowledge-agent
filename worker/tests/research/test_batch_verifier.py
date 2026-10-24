
from personal_brain.research.contracts import AtomicResearchDraft, VerificationResult
from personal_brain.research.verifier import verify_research_draft


class FakeJudge:
    """A fake verifier that records every claim it checks."""

    def __init__(self):
        self.checked_claims = []

    def verify(self, claim, evidence):
        self.checked_claims.append(claim.claimId)
        return VerificationResult(
            claimId=claim.claimId,
            verdict="supported",
            explanation="Fake test result",
        )


evidence = [
    {"id": "E001", "excerpt": "2025 年成長率為 20%。"},
    {"id": "E002", "excerpt": "2026 年預估成長率為 12%。"},
]

draft = AtomicResearchDraft.model_validate({
    "schemaVersion": "atomic-research-v1",
    "title": "營收分析",
    "summary": "預估成長率放緩",
    "findings": [{
        "findingId": "F001",
        "topic": "成長率比較",
        "claims": [
            {
                "claimId": "C001",
                "statement": "2025 年成長率為 20%。",
                "claimType": "fact",
                "citations": [{
                    "evidenceId": "E001",
                    "quote": "2025 年成長率為 20%。",
                    "relationType": "supports",
                }],
            },
            {
                "claimId": "C002",
                "statement": "2026 年預估成長率為 12%。",
                "claimType": "fact",
                "citations": [{
                    "evidenceId": "E002",
                    "quote": "2026 年預估成長率為 12%。",
                    "relationType": "supports",
                }],
            },
            {
                "claimId": "C003",
                "statement": "預估成長率下降 8 個百分點。",
                "claimType": "inference",
                "citations": [
                    {
                        "evidenceId": "E001",
                        "quote": "2025 年成長率為 20%。",
                        "relationType": "supports",
                    },
                    {
                        "evidenceId": "E002",
                        "quote": "2026 年預估成長率為 12%。",
                        "relationType": "supports",
                    },
                ],
            },
        ],
    }],
})

judge = FakeJudge()
results = verify_research_draft(draft, evidence, judge)

assert judge.checked_claims == ["C001", "C002", "C003"]
assert len(results) == 3
assert [result.claimId for result in results] == ["C001", "C002", "C003"]

print("PASS: All 3 atomic claims were verified")
print("PASS: Verification results match claim IDs")
