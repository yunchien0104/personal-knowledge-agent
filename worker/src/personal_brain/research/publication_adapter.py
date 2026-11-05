from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from personal_brain.research.orchestrator import ResearchExecution
from personal_brain.research_agent import (
    ValidatedResearchDraft,
    validate_synthesis,
)


def _template_report_body(
    execution: ResearchExecution,
    analysis_spec: Mapping[str, Any],
) -> str:
    parameters = analysis_spec.get("parameters")
    raw_sections = (
        parameters.get("sections") if isinstance(parameters, Mapping) else None
    )
    section_titles = [
        str(section.get("title")).strip()
        for section in raw_sections or []
        if isinstance(section, Mapping) and str(section.get("title") or "").strip()
    ]
    approved = set(execution.gate.approved_claim_ids)
    findings_by_topic = {
        finding.topic: finding for finding in execution.draft.findings
    }
    ordered = [
        findings_by_topic.pop(title)
        for title in section_titles
        if title in findings_by_topic
    ]
    ordered.extend(findings_by_topic.values())

    body = ["## Summary", "Verified research claims based on the supplied evidence."]
    for finding in ordered:
        claims = [claim for claim in finding.claims if claim.claimId in approved]
        if not claims:
            continue
        body.append(f"## {finding.topic}")
        for claim in claims:
            citations = " ".join(
                f"[Evidence {citation.evidenceId}]" for citation in claim.citations
            )
            if claim.claimType == "fact":
                uncertainty = "Source-supported fact."
            elif claim.claimType == "inference":
                uncertainty = "Evidence-based inference."
            else:
                uncertainty = "Evidence-supported hypothesis; not a proven fact."
            body.extend([f"- {claim.statement} {citations}", f"Uncertainty: {uncertainty}"])
    return "\n\n".join(body)


def adapt_r2_publication(
    execution: ResearchExecution,
    evidence: list[Mapping[str, Any]],
    *,
    analysis_spec: Mapping[str, Any] | None = None,
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

    validated = validate_synthesis(output, evidence)
    if analysis_spec is None:
        return validated
    body = _template_report_body(execution, analysis_spec)
    output_hash = sha256(json.dumps({
        "title": validated.title,
        "body": body,
        "findings": validated.findings,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ValidatedResearchDraft(
        title=validated.title,
        body=body,
        findings=validated.findings,
        output_hash=output_hash,
    )
