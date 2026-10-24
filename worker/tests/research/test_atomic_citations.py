
from copy import deepcopy

from personal_brain.research.contracts import AtomicResearchDraft
from personal_brain.research.evidence import (
    EvidenceValidationError,
    validate_atomic_citations,
)

# 模擬 R1 找到的證據
evidence = [
    {"id": "E001", "excerpt": "A 公司 2025 年營收成長率為 20%。"},
    {"id": "E002", "excerpt": "A 公司 2026 年預估營收成長率為 12%。"},
]

# 模擬 Generator 產生的 Atomic Research Draft
data = {
    "schemaVersion": "atomic-research-v1",
    "title": "A 公司營收分析",
    "summary": "A 公司預估營收成長率放緩。",
    "findings": [{
        "findingId": "F001",
        "topic": "營收成長趨勢",
        "claims": [{
            "claimId": "C001",
            "statement": "預估營收成長率下降 8 個百分點。",
            "claimType": "inference",
            "citations": [
                {
                    "evidenceId": "E001",
                    "quote": "2025 年營收成長率為 20%",
                    "relationType": "supports",
                },
                {
                    "evidenceId": "E002",
                    "quote": "2026 年預估營收成長率為 12%",
                    "relationType": "supports",
                },
            ],
        }],
    }],
}


def check_case(name, change=None, should_fail=False):
    test_data = deepcopy(data)
    test_evidence = deepcopy(evidence)

    if change:
        change(test_data, test_evidence)

    draft = AtomicResearchDraft.model_validate(test_data)

    try:
        validate_atomic_citations(draft, test_evidence)
        if should_fail:
            raise AssertionError(f"{name}: 錯誤資料竟然通過")
        print(f"PASS: {name}")
    except EvidenceValidationError:
        if not should_fail:
            raise
        print(f"PASS: {name}（成功攔截錯誤）")


check_case("正確引用")

check_case(
    "不存在的 Evidence ID",
    lambda d, e: d["findings"][0]["claims"][0]["citations"][0].update(
        evidenceId="E999"
    ),
    should_fail=True,
)

check_case(
    "錯誤引文",
    lambda d, e: d["findings"][0]["claims"][0]["citations"][0].update(
        quote="2025 年營收成長率為 99%"
    ),
    should_fail=True,
)

check_case(
    "重複引用",
    lambda d, e: d["findings"][0]["claims"][0]["citations"].append(
        deepcopy(d["findings"][0]["claims"][0]["citations"][0])
    ),
    should_fail=True,
)

check_case(
    "重複 Claim ID",
    lambda d, e: d["findings"][0]["claims"].append(
        deepcopy(d["findings"][0]["claims"][0])
    ),
    should_fail=True,
)
