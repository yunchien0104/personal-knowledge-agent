from __future__ import annotations
import unicodedata
from typing import Any, Mapping
from personal_brain.research.contracts import ResearchDraft
from personal_brain.research.contracts import AtomicResearchDraft

class EvidenceValidationError(ValueError):
    """Raised when a research citation is invalid."""


def _presentation_normalized_with_spans(
    value: str,
) -> tuple[str, list[tuple[int, int]]]:
    """Normalize presentation-only differences while retaining exact spans.

    Compatibility characters are normalized one source code point at a time,
    and each whitespace run becomes one space. Span boundaries prevent a quote
    from matching only part of an expanded compatibility character.
    """

    output: list[str] = []
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        if value[index].isspace():
            end = index + 1
            while end < len(value) and value[end].isspace():
                end += 1
            output.append(" ")
            spans.append((index, end))
            index = end
            continue
        normalized = unicodedata.normalize("NFKC", value[index])
        for character in normalized:
            output.append(character)
            spans.append((index, index + 1))
        index += 1
    return "".join(output), spans


def _unique_exact_presentation_span(quote: str, excerpt: str) -> str | None:
    normalized_quote, _ = _presentation_normalized_with_spans(quote.strip())
    normalized_excerpt, spans = _presentation_normalized_with_spans(excerpt)
    if not normalized_quote or not normalized_excerpt:
        return None
    candidates: set[tuple[int, int]] = set()
    start = normalized_excerpt.find(normalized_quote)
    while start >= 0:
        end = start + len(normalized_quote)
        starts_at_boundary = start == 0 or spans[start - 1] != spans[start]
        ends_at_boundary = end == len(spans) or spans[end - 1] != spans[end]
        if starts_at_boundary and ends_at_boundary:
            candidates.add((spans[start][0], spans[end - 1][1]))
        start = normalized_excerpt.find(normalized_quote, start + 1)
    if len(candidates) != 1:
        return None
    start, end = candidates.pop()
    return excerpt[start:end]


def normalize_atomic_citation_quotes(
    draft: AtomicResearchDraft,
    visible_evidence_rows: list[Mapping[str, Any]],
    original_evidence_rows: list[Mapping[str, Any]] | None = None,
) -> AtomicResearchDraft:
    """Resolve only unique presentation-equivalent quotes to exact source text.

    The Evidence ID is immutable. A repaired quote must map to exactly one span
    in the R2A-visible excerpt and that exact recovered substring must occur
    exactly once in the original Evidence. Semantic/fuzzy matching is forbidden.
    """

    def evidence_map(rows: list[Mapping[str, Any]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in rows:
            evidence_id, excerpt = row.get("id"), row.get("excerpt")
            if not isinstance(evidence_id, str) or not isinstance(excerpt, str):
                raise EvidenceValidationError("Invalid evidence input")
            if evidence_id in result:
                raise EvidenceValidationError(
                    f"Duplicate source evidence: {evidence_id}"
                )
            result[evidence_id] = excerpt
        return result

    visible = evidence_map(visible_evidence_rows)
    original = evidence_map(
        original_evidence_rows
        if original_evidence_rows is not None
        else visible_evidence_rows
    )
    normalized_findings = []
    for finding in draft.findings:
        normalized_claims = []
        for claim in finding.claims:
            normalized_citations = []
            for citation in claim.citations:
                evidence_id = citation.evidenceId
                if evidence_id not in visible or evidence_id not in original:
                    raise EvidenceValidationError(
                        f"Unknown evidence ID: {evidence_id}"
                    )
                quote = citation.quote
                if quote in visible[evidence_id] and quote in original[evidence_id]:
                    normalized_citations.append(citation)
                    continue
                resolved = _unique_exact_presentation_span(
                    quote, visible[evidence_id]
                )
                if (
                    resolved is None
                    or original[evidence_id].count(resolved) != 1
                ):
                    raise EvidenceValidationError(
                        f"Quote mismatch: {evidence_id}"
                    )
                normalized_citations.append(
                    citation.model_copy(update={"quote": resolved})
                )
            normalized_claims.append(
                claim.model_copy(update={"citations": normalized_citations})
            )
        normalized_findings.append(
            finding.model_copy(update={"claims": normalized_claims})
        )
    return draft.model_copy(update={"findings": normalized_findings})


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

                if (
                    not citation.quote.strip()
                    or citation.quote not in evidence_map[evidence_id]
            ):
                    raise EvidenceValidationError(
                        f"Quote mismatch: {evidence_id}"
                    )
