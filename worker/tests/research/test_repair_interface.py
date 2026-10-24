
from personal_brain.research.repair import evaluate_repair
from personal_brain.research.contracts import VerificationResult


class FakeRepairGenerator:
    def repair(
        self,
        question,
        plan,
        evidence,
        previous_draft,
        verification_feedback,
    ):
        return {
            **previous_draft,
            "summary": "Repaired draft",
        }


def test_repair_interface():
    feedback = [
        VerificationResult(
            claimId="C001",
            verdict="insufficient_evidence",
            explanation="Missing supporting evidence",
        )
    ]

    decision = evaluate_repair(feedback, attempts_used=0)

    assert decision.should_repair is True
    assert decision.claim_ids == ["C001"]

    generator = FakeRepairGenerator()

    repaired = generator.repair(
        question="Test question",
        plan={},
        evidence=[],
        previous_draft={"summary": "Original draft"},
        verification_feedback=[
            result.model_dump() for result in feedback
        ],
    )

    assert repaired["summary"] == "Repaired draft"

    print("PASS: Repair interface accepts verification feedback")


if __name__ == "__main__":
    test_repair_interface()
