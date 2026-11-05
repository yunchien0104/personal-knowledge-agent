from __future__ import annotations

import pytest

from personal_brain.research.contracts import AtomicResearchDraft
from personal_brain.research.evidence import (
    EvidenceValidationError,
    normalize_atomic_citation_quotes,
    validate_atomic_citations,
)


def _draft(evidence_id: str, quote: str) -> AtomicResearchDraft:
    return AtomicResearchDraft.model_validate({
        "schemaVersion": "atomic-research-v1",
        "title": "Citation normalization",
        "summary": "A source-grounded result.",
        "findings": [{
            "findingId": "finding-1",
            "topic": "Evidence",
            "claims": [{
                "claimId": "claim-1",
                "statement": "A bounded claim.",
                "claimType": "fact",
                "citations": [{
                    "evidenceId": evidence_id,
                    "quote": quote,
                    "relationType": "supports",
                }],
            }],
        }],
    })


def _quote(draft: AtomicResearchDraft) -> str:
    return draft.findings[0].claims[0].citations[0].quote


def test_unique_whitespace_difference_resolves_to_exact_visible_source() -> None:
    evidence_id = "evidence-1"
    exact = "CPO  將於\n2028 年進入量產。"
    visible = [{"id": evidence_id, "excerpt": f"前文。{exact}後文。"}]

    normalized = normalize_atomic_citation_quotes(
        _draft(evidence_id, "CPO 將於 2028 年進入量產。"), visible
    )

    assert _quote(normalized) == exact
    validate_atomic_citations(normalized, visible)


def test_unique_compatibility_characters_resolve_without_semantic_matching() -> None:
    evidence_id = "evidence-1"
    exact = "預測為１．６Ｔ。"
    visible = [{"id": evidence_id, "excerpt": exact}]

    normalized = normalize_atomic_citation_quotes(
        _draft(evidence_id, "預測為1.6T。"), visible
    )

    assert _quote(normalized) == exact


def test_multiple_presentation_equivalent_ranges_fail_closed() -> None:
    evidence_id = "evidence-1"
    visible = [{
        "id": evidence_id,
        "excerpt": "CPO  2028。另一段 CPO\n2028。",
    }]

    with pytest.raises(EvidenceValidationError, match="Quote mismatch"):
        normalize_atomic_citation_quotes(
            _draft(evidence_id, "CPO 2028。"), visible
        )


def test_text_present_only_in_original_but_omitted_from_shaped_payload_is_rejected() -> None:
    evidence_id = "evidence-1"
    visible = [{"id": evidence_id, "excerpt": "保留段落。"}]
    original = [{"id": evidence_id, "excerpt": "保留段落。被省略的重要段落。"}]

    with pytest.raises(EvidenceValidationError, match="Quote mismatch"):
        normalize_atomic_citation_quotes(
            _draft(evidence_id, "被省略的重要段落。"), visible, original
        )


def test_artificial_join_between_shaped_segments_is_not_a_source_range() -> None:
    evidence_id = "evidence-1"
    visible = [{"id": evidence_id, "excerpt": "第一段\n第二段"}]
    original = [{"id": evidence_id, "excerpt": "第一段，中間文字，第二段"}]

    with pytest.raises(EvidenceValidationError, match="Quote mismatch"):
        normalize_atomic_citation_quotes(
            _draft(evidence_id, "第一段 第二段"), visible, original
        )


def test_reworded_quote_is_never_repaired() -> None:
    evidence_id = "evidence-1"
    visible = [{"id": evidence_id, "excerpt": "來源預測 2028 年可能量產。"}]

    with pytest.raises(EvidenceValidationError, match="Quote mismatch"):
        normalize_atomic_citation_quotes(
            _draft(evidence_id, "產業將在 2028 年正式量產。"), visible
        )
