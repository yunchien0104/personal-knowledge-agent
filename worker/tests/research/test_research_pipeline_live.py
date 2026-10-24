
import json

from personal_brain.research.llm import OpenAICompatibleResearchModel
from personal_brain.research.verifier import LLMClaimJudge
from personal_brain.research.orchestrator import ResearchOrchestrator


# 模擬 R1 Retriever 已經找到的證據
EVIDENCE = [
    {
        "id": "E001",
        "excerpt": "A 公司 2024 年營收為 100 億元。",
    },
    {
        "id": "E002",
        "excerpt": "A 公司 2025 年營收為 120 億元。",
    },
]

QUESTION = "A 公司 2025 年營收成長情況如何？"


def main():
    generator = OpenAICompatibleResearchModel()

    # Generator 與 Verifier 使用目前設定的模型
    judge = LLMClaimJudge(generator.client)

    orchestrator = ResearchOrchestrator(generator, judge)

    execution = orchestrator.run(
        question=QUESTION,
        plan={"question": QUESTION},
        evidence=EVIDENCE,
    )

    print("\n=== GATE ===")
    print("Can publish:", execution.gate.can_publish)
    print("Repair attempts:", execution.repair_attempts)

    print("\n=== VERIFICATION ===")
    for result in execution.verification_results:
        print(
            result.claimId,
            result.verdict,
            result.explanation,
        )

    print("\n=== REPORT ===")
    if execution.verified_report is not None:
        print(execution.verified_report)
    else:
        print("No verified report generated")

    print("\n=== EXECUTION SUMMARY ===")
    print(json.dumps({
        "claims": sum(
            len(finding.claims)
            for finding in execution.draft.findings
        ),
        "can_publish": execution.gate.can_publish,
        "repair_attempts": execution.repair_attempts,
    }, indent=2))


if __name__ == "__main__":
    main()
