
import json
from pathlib import Path


DATASET = (
    Path(__file__).parent
    / "eval_data"
    / "claim_verification_v1_100.json"
)

VERDICTS = {
    "supported",
    "unsupported",
    "insufficient_evidence",
    "contradicted",
}


def load_dataset():
    with DATASET.open(encoding="utf-8") as file:
        cases = json.load(file)

    if not isinstance(cases, list) or not cases:
        raise ValueError("Evaluation dataset must be a non-empty list")

    ids = set()

    for case in cases:
        case_id = case["case_id"]
        expected = case["expected_verdict"]

        claim_type = case["claim_type"]

        if claim_type not in {"fact", "inference", "hypothesis"}:
            raise ValueError(f"Invalid claim type: {claim_type}")

        if case_id in ids:
            raise ValueError(f"Duplicate case ID: {case_id}")

        if expected not in VERDICTS:
            raise ValueError(f"Invalid verdict: {expected}")

        ids.add(case_id)

    return cases


def score_predictions(cases, predictions):
    if len(cases) != len(predictions):
        raise ValueError("Prediction count does not match dataset")

    correct = 0
    false_accepts = 0
    actual_negative = 0
    details = []

    for case, predicted in zip(cases, predictions):
        expected = case["expected_verdict"]

        if predicted not in VERDICTS:
            raise ValueError(f"Invalid prediction: {predicted}")

        if predicted == expected:
            correct += 1

        # 非 supported 的案例，本來都不應放行
        if expected != "supported":
            actual_negative += 1

            if predicted == "supported":
                false_accepts += 1

        details.append({
            "case_id": case["case_id"],
            "expected": expected,
            "predicted": predicted,
            "correct": predicted == expected,
        })

    total = len(cases)

    return {
        "total": total,
        "accuracy": correct / total,
        "false_acceptance_rate": (
            false_accepts / actual_negative
            if actual_negative
            else None
        ),
        "details": details,
    }


if __name__ == "__main__":
    cases = load_dataset()

    # 第一輪先使用標準答案測試計分器本身
    predictions = [
        case["expected_verdict"]
        for case in cases
    ]

    report = score_predictions(cases, predictions)

    print(f"Cases: {report['total']}")
    print(f"Accuracy: {report['accuracy']:.1%}")

    if report["false_acceptance_rate"] is not None:
        print(
            "False Acceptance Rate: "
            f"{report['false_acceptance_rate']:.1%}"
        )

    print("PASS: Evaluation scoring logic")

    