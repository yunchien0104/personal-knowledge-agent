
from __future__ import annotations

from personal_brain.research.contracts import AtomicResearchDraft
from personal_brain.research.gate import GateDecision


def build_verified_report(
    draft: AtomicResearchDraft,
    gate: GateDecision,
) -> str:
    """Build a report using only gate-approved claims."""

    if not gate.can_publish:
        raise ValueError("Research draft did not pass verification gate")

    approved = set(gate.approved_claim_ids)

    sections = [f"# {draft.title}"]

    for finding in draft.findings:
        sections.append(f"## {finding.findingId}")

        for claim in finding.claims:
            if claim.claimId not in approved:
                raise ValueError(
                    f"Unapproved claim: {claim.claimId}"
                )

            citations = " ".join(
                f"[Evidence {c.evidenceId}]"
                for c in claim.citations
            )

            sections.append(
                f"- {claim.statement} {citations}"
            )

    return "\n\n".join(sections)
