
import os

from personal_brain.llm_client import StructuredLLMClient
from personal_brain.research.contracts import (
    EvidenceCitation,
    ResearchClaim,
)
from personal_brain.research.verifier import LLMClaimJudge

client = StructuredLLMClient(
    base_url=os.environ["LLM_API_BASE_URL"],
    api_key=os.environ["LLM_API_KEY"],
    model=os.environ["RESEARCH_MODEL"],
)

judge = LLMClaimJudge(client)

claim = ResearchClaim(
    claimId="C001",
    statement="A 公司營收成長率預期放緩。",
    claimType="inference",
    citations=[
        EvidenceCitation(
            evidenceId="E001",
            quote="A 公司 2025 年營收成長率為 20%。",
            relationType="supports",
        )
    ],
)

evidence = [
    {
        "id": "E001",
        "excerpt": "A 公司 2025 年營收成長率為 20%。",
    }
]

result = judge.verify(claim, evidence)

print(result.model_dump())
