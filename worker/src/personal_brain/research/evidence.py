from __future__ import annotations
from typing import Any, Mapping
from personal_brain.research.contracts import ResearchDraft
from personal_brain.research.contracts import AtomicResearchDraft

class EvidenceValidationError(ValueError):
    """Raised when a research citation is invalid."""


def validate_research_citations(
    draft: ResearchDraft,
    evidence_rows: list[Mapping[str, Any]],
) -> None:
    """Validate citation IDs, quotes, and duplicates."""

    evidence_map = {
        row["id"]: row
        for row in evidence_rows
    }

    for finding in draft.findings:
        seen_ids: set[str] = set()

        for citation in finding.citations:
            evidence_id = citation.evidenceId

            if evidence_id not in evidence_map:
                raise EvidenceValidationError(
                    f"Unknown evidence ID: {evidence_id}"
                )

            if evidence_id in seen_ids:
                raise EvidenceValidationError(
                    f"Duplicate evidence ID: {evidence_id}"
                )

            seen_ids.add(evidence_id)

            source_excerpt = evidence_map[evidence_id]["excerpt"]

            if citation.quote not in source_excerpt:
                raise EvidenceValidationError(
                    f"Quote does not match evidence: {evidence_id}"
                )


def validate_atomic_citations(
    draft: AtomicResearchDraft,
    evidence_rows: list[Mapping[str, Any]],
) -> None:
    """Validate citations for every atomic claim."""

    evidence_map = {}

    for row in evidence_rows:
        evidence_id = row.get("id")
        excerpt = row.get("excerpt")

        if (
            not isinstance(evidence_id, str)
            or not isinstance(excerpt, str)
            or not excerpt.strip()
        ):
            raise EvidenceValidationError(
                "Invalid evidence input"
            )

        if evidence_id in evidence_map:
            raise EvidenceValidationError(
                f"Duplicate source evidence: {evidence_id}"
            )

        evidence_map[evidence_id] = excerpt

    seen_claim_ids = set()

    for finding in draft.findings:
        for claim in finding.claims:

            if claim.claimId in seen_claim_ids:
                raise EvidenceValidationError(
                    f"Duplicate claim ID: {claim.claimId}"
                )

            seen_claim_ids.add(claim.claimId)
            seen_evidence_ids = set()

            for citation in claim.citations:

                evidence_id = citation.evidenceId

                if evidence_id not in evidence_map:
                    raise EvidenceValidationError(
                        f"Unknown evidence ID: {evidence_id}"
                    )

                if evidence_id in seen_evidence_ids:
                    raise EvidenceValidationError(
                        f"Duplicate citation: {evidence_id}"
                    )

                seen_evidence_ids.add(evidence_id)

                if citation.quote not in evidence_map[evidence_id]:
                    raise EvidenceValidationError(
                        f"Quote mismatch: {evidence_id}"
                    )
