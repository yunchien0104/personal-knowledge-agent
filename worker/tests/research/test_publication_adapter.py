
from personal_brain.research.contracts import (
    AtomicResearchDraft,
    VerificationResult,
)
from personal_brain.research.gate import GateDecision
from personal_brain.research.orchestrator import ResearchExecution
from personal_brain.research.publication_adapter import adapt_r2_publication


EVIDENCE = [
    {"id": "E001", "excerpt": "A 公司 2024 年營收為 100 億元。"},
    {"id": "E002", "excerpt": "A 公司 2025 年營收為 120 億元。"},
]

DRAFT = AtomicResearchDraft.model_validate({
    "schemaVersion": "atomic-research-v1",
    "title": "A 公司營收分析",
    "summary": "公司未來可能破產。",  # 未驗證，不得發布
    "findings": [{
        "findingId": "F001",
        "topic": "營收",
        "claims": [
            {
                "claimId": "C001",
                "statement": "A 公司 2025 年營收為 120 億元。",
                "claimType": "fact",
                "citations": [{
                    "evidenceId": "E002",
                    "quote": "A 公司 2025 年營收為 120 億元。",
                    "relationType": "supports",
                }],
            },
            {
                "claimId": "C002",
                "statement": "A 公司營收增加 20 億元。",
                "claimType": "inference",
                "citations": [
                    {
                        "evidenceId": "E001",
                        "quote": "A 公司 2024 年營收為 100 億元。",
                        "relationType": "supports",
                    },
                    {
                        "evidenceId": "E002",
                        "quote": "A 公司 2025 年營收為 120 億元。",
                        "relationType": "supports",
                    },
                ],
            },
        ],
    }],
})


def make_execution(can_publish):
    claim_ids = ["C001", "C002"]

    return ResearchExecution(
        draft=DRAFT,
        verification_results=[
            VerificationResult(
                claimId=cid,
                verdict="supported" if can_publish else "unsupported",
                explanation="Test result",
            )
            for cid in claim_ids
        ],
        gate=GateDecision(
            approved_claim_ids=claim_ids if can_publish else [],
            rejected_claim_ids=[],
            repair_claim_ids=[] if can_publish else claim_ids,
            can_publish=can_publish,
        ),
        verified_report="Verified report" if can_publish else None,
    )


def test_success():
    publication = adapt_r2_publication(
        make_execution(True),
        EVIDENCE,
    )

    assert len(publication.findings) == 2
    assert publication.findings[0]["text"] == DRAFT.findings[0].claims[0].statement
    assert publication.findings[1]["text"] == DRAFT.findings[0].claims[1].statement

    assert publication.findings[0]["citations"][0]["evidenceId"] == "E002"
    assert len(publication.findings[1]["citations"]) == 2

    assert "公司未來可能破產" not in publication.body
    assert publication.output_hash

    print("PASS: R2 claims converted to R1 publication format")


def test_blocked():
    try:
        adapt_r2_publication(make_execution(False), EVIDENCE)
    except ValueError:
        print("PASS: Blocked R2 draft cannot be published")
    else:
        raise AssertionError("Blocked draft was accepted")


if __name__ == "__main__":
    test_success()
    test_blocked()
