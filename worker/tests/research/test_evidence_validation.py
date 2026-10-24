from personal_brain.research.contracts import ResearchDraft
from personal_brain.research.evidence import (
    EvidenceValidationError,
    validate_research_citations,
)

evidence = [
    {"id": "E001", "excerpt": "A 公司 2025 年營收成長率為 20%。"},
    {"id": "E002", "excerpt": "A 公司 2026 年預估營收成長率為 12%。"},
]

draft = ResearchDraft.model_validate({
    "title": "A 公司研究",
    "summary": "營收成長預期放緩",
    "findings": [{
        "text": "營收成長率預期放緩",
        "uncertainty": "2026 年為預估值",
        "citations": [{
            "evidenceId": "E001",
            "quote": "2025 年營收成長率為 20%",
            "relationType": "supports",
        }],
    }],
})

validate_research_citations(draft, evidence)
print("PASS：正確引用通過")

# 測試錯誤的 Evidence ID
bad_draft = draft.model_copy(deep=True)
bad_draft.findings[0].citations[0].evidenceId = "E999"

try:
    validate_research_citations(bad_draft, evidence)
    print("FAIL：錯誤 ID 竟然通過")
except EvidenceValidationError:
    print("PASS：錯誤 Evidence ID 被拒絕")