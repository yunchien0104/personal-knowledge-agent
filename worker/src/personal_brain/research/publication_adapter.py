
from __future__ import annotations

from typing import Any, Mapping

from personal_brain.research.orchestrator import ResearchExecution
from personal_brain.research_agent import (
    ValidatedResearchDraft,
    validate_synthesis,
)


def adapt_r2_publication(
    execution: ResearchExecution,
    evidence: list[Mapping[str, Any]],
) -> ValidatedResearchDraft:
    """Convert verified atomic claims into the existing R1 format."""

    if not execution.gate.can_publish:
        raise ValueError("R2 research did not pass verification")

    if execution.verified_report is None:
        raise ValueError("Verified report is missing")

    claim_ids = [
        claim.claimId
        for finding in execution.draft.findings
        for claim in finding.claims
    ]

    results = execution.verification_results

    if (
        not claim_ids
        or len(set(claim_ids)) != len(claim_ids)
        or len(results) != len(claim_ids)
        or {r.claimId for r in results} != set(claim_ids)
        or any(r.verdict != "supported" for r in results)
    ):
        raise ValueError(
            "Publication requires every claim to be verified and supported"
        )

    findings = []

    for finding in execution.draft.findings:
        for claim in finding.claims:
            if claim.claimType == "fact":
                uncertainty = "Source-supported fact."
            elif claim.claimType == "inference":
                uncertainty = "Evidence-based inference."
            else:
                uncertainty = "Evidence-supported hypothesis; not a proven fact."

            findings.append({
                "text": claim.statement,
                "uncertainty": uncertainty,
                "citations": [
                    citation.model_dump()
                    for citation in claim.citations
                ],
            })

    if not findings:
        raise ValueError("No verified claims to publish")

    # Do not publish the original, unverified summary.
    output = {
        "title": execution.draft.title,
        "summary": "Verified research claims based on the supplied evidence.",
        "findings": findings,
    }

    return validate_synthesis(output, evidence)
