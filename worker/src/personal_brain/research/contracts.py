"""Data contracts for Personal Brain Research Agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )


class ResearchDraft(StrictModel):
    title: str
    summary: str
    findings: list[ResearchFinding] = Field(min_length=1)


class ResearchFinding(StrictModel):
    text: str
    uncertainty: str
    citations: list[EvidenceCitation] = Field(min_length=1)


class ResearchClaim(StrictModel):
    claimId: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    claimType: Literal[
        "fact",
        "inference",
        "hypothesis",
    ]
    citations: list[EvidenceCitation]

class EvidenceCitation(StrictModel):
    evidenceId: str
    quote: str
    relationType: Literal[
        "supports",
        "contradicts",
        "contextualizes",
    ]


class VerificationResult(StrictModel):
    claimId: str = Field(min_length=1)
    verdict: Literal[
        "supported",
        "unsupported",
        "insufficient_evidence",
        "contradicted",
    ]
    explanation: str = Field(min_length=1)

class VerifiedResearchFinding(StrictModel):
    text: str = Field(min_length=1)
    uncertainty: str
    claims: list[ResearchClaim] = Field(min_length=1)


class VerifiedResearchDraft(StrictModel):
    title: str = Field(min_length=1)
    summary: str
    findings: list[VerifiedResearchFinding] = Field(min_length=1)


class AtomicResearchFinding(StrictModel):
    findingId: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    claims: list[ResearchClaim] = Field(min_length=1)


class AtomicResearchDraft(StrictModel):
    schemaVersion: Literal["atomic-research-v1"] = "atomic-research-v1"
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    findings: list[AtomicResearchFinding] = Field(min_length=1)
