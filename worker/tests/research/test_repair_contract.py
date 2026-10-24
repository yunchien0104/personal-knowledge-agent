
from copy import deepcopy

from personal_brain.research.contracts import (
    AtomicResearchDraft,
    VerificationResult,
)
from personal_brain.research.repair import validate_repaired_draft


DATA = {
    "schemaVersion": "atomic-research-v1",
    "title": "A 公司研究",
    "summary": "營收成長分析",
    "findings": [{
        "findingId": "F001",
        "topic": "營收",
        "claims": [
            {
                "claimId": "C001",
                "statement": "2025 年成長率為 20%。",
                "claimType": "fact",
                "citations": [{
                    "evidenceId": "E001",
                    "quote": "2025 年成長率為 20%",
                    "relationType": "supports",
                }],
            },
            {
                "claimId": "C002",
                "statement": "市場需求正在崩潰。",
                "claimType": "inference",
                "citations": [],
            },
        ],
    }],
}

RESULTS = [
    VerificationResult(
        claimId="C001",
        verdict="supported",
        explanation="Evidence supports this claim",
    ),
    VerificationResult(
        claimId="C002",
        verdict="insufficient_evidence",
        explanation="No supporting evidence",
    ),
]


def validate(data):
    original = AtomicResearchDraft.model_validate(DATA)
    repaired = AtomicResearchDraft.model_validate(data)
    validate_repaired_draft(original, repaired, RESULTS)


# 1. 修正 C002，保留 C001
valid = deepcopy(DATA)
valid["findings"][0]["claims"][1]["statement"] = "目前證據不足。"
validate(valid)
print("PASS: Repair can modify an unverified claim")


# 2. 禁止偷改已通過的 C001
invalid = deepcopy(DATA)
invalid["findings"][0]["claims"][0]["statement"] = "2025 年成長率為 99%。"

try:
    validate(invalid)
except ValueError:
    print("PASS: Supported claim modification blocked")
else:
    raise AssertionError("Modified supported claim was accepted")


# 3. 禁止新增 C999
invalid = deepcopy(DATA)
new_claim = deepcopy(invalid["findings"][0]["claims"][1])
new_claim["claimId"] = "C999"
invalid["findings"][0]["claims"].append(new_claim)

try:
    validate(invalid)
except ValueError:
    print("PASS: New claim ID blocked")
else:
    raise AssertionError("New claim ID was accepted")


# 4. 禁止刪除 C001
invalid = deepcopy(DATA)
invalid["findings"][0]["claims"].pop(0)

try:
    validate(invalid)
except ValueError:
    print("PASS: Supported claim removal blocked")
else:
    raise AssertionError("Supported claim removal was accepted")
