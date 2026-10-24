
from personal_brain.research.llm import OpenAICompatibleResearchModel
from personal_brain.research.contracts import AtomicResearchDraft
from personal_brain.research.evidence import validate_atomic_citations

question = "A 公司 2026 年預估營收成長率是否放緩？"

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

generator = OpenAICompatibleResearchModel()
result = generator.synthesize(question, plan, evidence)
draft = AtomicResearchDraft.model_validate(result)
validate_atomic_citations(draft, evidence)
print("PASS: Atomic Citation Validation")
print("Title:", draft.title)
print("Findings:", len(draft.findings))

for finding in draft.findings:
    print("Finding:", finding.topic)
    for claim in finding.claims:
        print(" ", claim.claimId, claim.claimType, claim.statement)
        print("  Evidence:", [c.evidenceId for c in claim.citations])
