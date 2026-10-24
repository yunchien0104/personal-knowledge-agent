
import os

from personal_brain.research.llm import OpenAICompatibleResearchModel

# 測試前確認環境變數已設定
for key in ("LLM_API_BASE_URL", "LLM_API_KEY", "RESEARCH_MODEL"):
    if not os.environ.get(key):
        raise RuntimeError(f"Missing environment variable: {key}")

model = OpenAICompatibleResearchModel()

question = "A 公司的營收成長是否正在放緩？"

plan = {
    "question": question,
    "scope": "Test evidence only",
    "subquestions": [question],
    "evidenceRequirements": ["Use supplied evidence only"],
    "maxCostUsd": 0,
    "maxQueries": 1,
    "maxRounds": 1,
    "stopConditions": ["Stop after one round"],
}

evidence = [
    {
        "id": "E001",
        "excerpt": "A 公司 2025 年營收成長率為 20%。",
    },
    {
        "id": "E002",
        "excerpt": "A 公司 2026 年預估營收成長率為 12%。",
    },
]

result = model.synthesize(question, plan, evidence)

print("=== Research Generator Output ===")
print(result)
