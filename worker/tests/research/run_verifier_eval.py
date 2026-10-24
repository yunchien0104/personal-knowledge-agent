
import json
import os
from pathlib import Path

from personal_brain.llm_client import StructuredLLMClient
from personal_brain.research.contracts import ResearchClaim
from personal_brain.research.verifier import LLMClaimJudge

from eval_runner import load_dataset, score_predictions


def main():
    cases = load_dataset()

    case_id = os.getenv("EVAL_CASE_ID")

    if case_id:
        cases = [
            case for case in cases
            if case["case_id"] == case_id
        ]

        if not cases:
            raise ValueError(f"Case not found: {case_id}")

    limit = int(os.getenv("EVAL_LIMIT", "0"))
    if limit > 0:
        cases = cases[:limit]

    client = StructuredLLMClient(
        base_url=os.environ["LLM_API_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ["RESEARCH_MODEL"],
    )
    judge = LLMClaimJudge(client)

    predictions = []

    for case in cases:
        evidence = case["evidence"]

        claim = ResearchClaim.model_validate({
            "claimId": case["case_id"],
            "statement": case["claim"],
            "claimType": case["claim_type"],
            "citations": [
                {
                    "evidenceId": row["id"],
                    "quote": row["excerpt"],
                    "relationType": "supports",
                }
                for row in evidence
            ],
        })

        result = judge.verify(claim, evidence)
        predictions.append(result.verdict)
        print(f"Reason: {result.explanation}")

        print(
            f"{case['case_id']}: "
            f"expected={case['expected_verdict']} "
            f"predicted={result.verdict}"
        )

    report = score_predictions(cases, predictions)

    print()
    print(f"Cases: {report['total']}")
    print(f"Accuracy: {report['accuracy']:.1%}")

    far = report["false_acceptance_rate"]
    if far is not None:
        print(f"False Acceptance Rate: {far:.1%}")

    output = (
        Path(__file__).parent
        / "eval_data"
        / "verifier_eval_results.json"
    )

    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Results saved: {output}")


if __name__ == "__main__":
    main()
