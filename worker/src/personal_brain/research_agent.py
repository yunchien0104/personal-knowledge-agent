from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Protocol

from personal_brain.queue_runtime import LeaseGuard, LeaseLostError, ProcessResult
from personal_brain.source_notes import ModelError, _text
from personal_brain.supabase_gateway import ClaimedJob, GatewayError
from personal_brain.research.orchestrator import ResearchOrchestrator
from pydantic import ValidationError

PROMPT_VERSION = "research-internal-v1"
MAX_EVIDENCE = 12
R2_PROMPT_VERSION = "research-r2-v1"


class StructuredResearchModel(Protocol):
    provider: str
    model_name: str

    def plan(self, question: str) -> Mapping[str, Any]: ...

    def synthesize(
        self, question: str, plan: Mapping[str, Any], evidence: list[Mapping[str, Any]]
    ) -> Mapping[str, Any]: ...


class LocalExtractiveResearchModel:
    """Zero-cost, deterministic MVP adapter; it never calls an external provider."""

    provider = "local"
    model_name = "extractive-research-v1"

    def plan(self, question: str) -> Mapping[str, Any]:
        query = " ".join(question.split())[:200]
        return {
            "question": question,
            "scope": "Internal Personal Brain evidence only",
            "subquestions": [query],
            "evidenceRequirements": [
                "Every finding must cite active original Evidence with a verbatim quote."
            ],
            "maxCostUsd": 0,
            "maxQueries": 1,
            "maxRounds": 1,
            "stopConditions": [
                "Stop after one bounded internal retrieval round.",
                "Return insufficient evidence rather than inventing content.",
            ],
        }

    def synthesize(
        self, question: str, plan: Mapping[str, Any], evidence: list[Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        del plan
        findings = []
        for row in evidence[:3]:
            excerpt = str(row["excerpt"]).strip()
            quote = excerpt[:4000]
            findings.append({
                "text": excerpt[:8000],
                "uncertainty": "This reflects one internal source and is not independent verification.",
                "citations": [{
                    "evidenceId": row["id"],
                    "quote": quote,
                    "relationType": "contextualizes",
                }],
            })
        return {
            "title": f"Research Draft: {question}"[:300],
            "summary": "Bounded internal retrieval produced the following source-grounded findings.",
            "findings": findings,
        }


@dataclass(frozen=True, slots=True)
class ValidatedResearchDraft:
    title: str
    body: str
    findings: list[dict[str, Any]]
    output_hash: str


def validate_plan(output: Mapping[str, Any], question: str) -> dict[str, Any]:
    expected = {
        "question", "scope", "subquestions", "evidenceRequirements",
        "maxCostUsd", "maxQueries", "maxRounds", "stopConditions",
    }
    if set(output) != expected or output.get("question") != question:
        raise ModelError("invalid_research_plan", "Research plan is invalid", retryable=False)
    scope = _text(output.get("scope"), maximum=1000)
    subquestions = output.get("subquestions")
    requirements = output.get("evidenceRequirements")
    stop_conditions = output.get("stopConditions")
    if (
        not isinstance(subquestions, list) or not 1 <= len(subquestions) <= 5
        or not isinstance(requirements, list) or not 1 <= len(requirements) <= 10
        or not isinstance(stop_conditions, list) or not 1 <= len(stop_conditions) <= 10
        or type(output.get("maxQueries")) is not int
        or not 1 <= output["maxQueries"] <= 3
        or output.get("maxRounds") != 1
        or output.get("maxCostUsd") != 0
    ):
        raise ModelError("invalid_research_plan", "Research plan is invalid", retryable=False)
    return {
        "question": question,
        "scope": scope,
        "subquestions": [_text(value, maximum=200) for value in subquestions],
        "evidenceRequirements": [_text(value, maximum=1000) for value in requirements],
        "maxCostUsd": 0,
        "maxQueries": output["maxQueries"],
        "maxRounds": 1,
        "stopConditions": [_text(value, maximum=1000) for value in stop_conditions],
    }


def select_evidence(context: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = context.get("evidence")
    if not isinstance(rows, list):
        raise ModelError("invalid_research_context", "Research context is invalid", retryable=False)
    selected: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ModelError("invalid_research_context", "Research context is invalid", retryable=False)
        evidence_id = row.get("id")
        excerpt = row.get("excerpt")
        locator = row.get("locator")
        if (
            not isinstance(evidence_id, str) or evidence_id in seen
            or not isinstance(excerpt, str) or not excerpt.strip()
            or not isinstance(locator, dict)
            or not isinstance(row.get("assetId"), str)
            or not isinstance(row.get("revisionId"), str)
        ):
            continue
        seen.add(evidence_id)
        selected.append(row)
        if len(selected) == MAX_EVIDENCE:
            break
    if not selected:
        raise ModelError(
            "insufficient_evidence",
            "No matching original Evidence was found in the internal knowledge base",
            retryable=False,
        )
    return selected


def validate_synthesis(
    output: Mapping[str, Any], evidence_rows: list[Mapping[str, Any]]
) -> ValidatedResearchDraft:
    if set(output) != {"title", "summary", "findings"}:
        raise ModelError("invalid_model_output", "Research synthesis is invalid", retryable=False)
    title = _text(output.get("title"), maximum=300)
    summary = _text(output.get("summary"), maximum=12000)
    findings = output.get("findings")
    if not isinstance(findings, list) or not 1 <= len(findings) <= 100:
        raise ModelError("invalid_model_output", "Research synthesis needs findings", retryable=False)
    evidence = {str(row["id"]): row for row in evidence_rows}
    clean_findings: list[dict[str, Any]] = []
    rendered = ["## Summary", summary, "## Findings"]
    for finding_index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict) or set(finding) != {"text", "uncertainty", "citations"}:
            raise ModelError("invalid_model_output", "Research finding is invalid", retryable=False)
        text = _text(finding.get("text"), maximum=8000)
        uncertainty = _text(finding.get("uncertainty"), maximum=2000)
        citations = finding.get("citations")
        if not isinstance(citations, list) or not 1 <= len(citations) <= 20:
            raise ModelError("unsupported_research_finding", "Every finding needs citations", retryable=False)
        clean_citations: list[dict[str, str]] = []
        seen: set[str] = set()
        for citation in citations:
            if not isinstance(citation, dict) or set(citation) != {
                "evidenceId", "quote", "relationType"
            }:
                raise ModelError("invalid_model_output", "Research citation is invalid", retryable=False)
            evidence_id = citation.get("evidenceId")
            quote = _text(citation.get("quote"), maximum=4000)
            relation = citation.get("relationType")
            row = evidence.get(evidence_id) if isinstance(evidence_id, str) else None
            if (
                row is None or evidence_id in seen
                or quote not in str(row.get("excerpt", ""))
                or relation not in {"supports", "contradicts", "contextualizes"}
            ):
                raise ModelError(
                    "unsupported_research_finding",
                    "A finding is not supported by selected original Evidence",
                    retryable=False,
                )
            seen.add(evidence_id)
            clean_citations.append({
                "evidenceId": evidence_id,
                "quote": quote,
                "relationType": relation,
            })
        clean_findings.append({
            "text": text, "uncertainty": uncertainty, "citations": clean_citations
        })
        markers = " ".join(f"[Evidence {item['evidenceId']}]" for item in clean_citations)
        rendered.extend([f"### Finding {finding_index}", text, markers, f"Uncertainty: {uncertainty}"])
    canonical = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return ValidatedResearchDraft(
        title, "\n\n".join(rendered), clean_findings, sha256(canonical).hexdigest()
    )


class ResearchAgentWorker:
    def __init__(
        self,
        gateway: Any,
        model: StructuredResearchModel,
        worker_id: str,
        *,
        orchestrator: ResearchOrchestrator,
        lease_seconds: int = 120,
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.worker_id = worker_id
        self.orchestrator = orchestrator
        self.lease_seconds = lease_seconds


    
    def process_one(self) -> ProcessResult:
        from personal_brain.research.publication_adapter import (
            adapt_r2_publication,
        )

        job: ClaimedJob | None = self.gateway.claim(
            self.worker_id, self.lease_seconds, "research"
        )
        if job is None:
            return ProcessResult("idle")

        try:
            if job.payload.job_type != "execute_research":
                raise GatewayError(
                    "invalid_claim",
                    "Worker received an unsupported Job",
                    retryable=False,
                )

            question = job.payload.input.get("question")
            if not isinstance(question, str) or not question.strip():
                raise GatewayError(
                    "invalid_research_context",
                    "Research question is unavailable",
                    retryable=False,
                )

            # Heartbeat must remain active through R2 and publication.
            with LeaseGuard(
                self.gateway, job, self.worker_id, self.lease_seconds
            ) as lease:

                # 1. Planning
                plan = validate_plan(
                    self.model.plan(question), question
                )
                lease.pulse()

                # 2. R1 Retrieval
                context = self.gateway.research_context(
                    job, self.worker_id, plan
                )
                if context is None:
                    raise LeaseLostError(
                        "Worker lease is no longer valid"
                    )

                evidence = select_evidence(context)
                lease.pulse()

                # 3. R2 Generate -> Verify -> Repair -> Gate
                try:
                    execution = self.orchestrator.run(
                        question=question,
                        plan=plan,
                        evidence=evidence,
                    )
                except (RuntimeError, TimeoutError) as error:
                    raise ModelError(
                        "r2_llm_failure",
                        "R2 research model request failed",
                        retryable=True,
                    ) from error
                except (
                    ValueError,
                    KeyError,
                    TypeError,
                    IndexError,
                    ValidationError,
                ) as error:
                    raise ModelError(
                        "r2_invalid_output",
                        "R2 research returned an invalid result",
                        retryable=False,
                    ) from error

                lease.pulse()

                # 4. Verification Gate
                if not execution.gate.can_publish:
                    raise ModelError(
                        "research_verification_failed",
                        "Research claims did not pass verification",
                        retryable=False,
                    )

                # 5. Convert verified claims to publication format
                try:
                    draft = adapt_r2_publication(
                        execution, evidence
                    )
                except (ValueError, ModelError) as error:
                    raise ModelError(
                        "r2_publication_invalid",
                        "R2 publication draft is invalid",
                        retryable=False,
                    ) from error

                lease.pulse()

                # 6. Supabase Publication
                publication = self.gateway.publish_research_draft(
                    job,
                    self.worker_id,
                    model_provider=self.orchestrator.generator.provider,
                    model_name=self.orchestrator.generator.model_name,
                    prompt_version=R2_PROMPT_VERSION,
                    output_hash=draft.output_hash,
                    title=draft.title,
                    body=draft.body,
                    findings=draft.findings,
                )

                if publication is None:
                    raise LeaseLostError(
                        "Worker lease expired before publication"
                    )

                note_id = publication.get("noteId")
                if not isinstance(note_id, str):
                    raise GatewayError(
                        "invalid_publication_response",
                        "Research publication is invalid",
                        retryable=True,
                    )

                return ProcessResult(
                    "completed", job.id, note_id=note_id
                )

        except LeaseLostError:
            return ProcessResult("lease_lost", job.id)

        except (ModelError, GatewayError) as error:
            self.gateway.report_failure(
                job,
                self.worker_id,
                error_class=(
                    "transient" if error.retryable else "permanent"
                ),
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
            )
            return ProcessResult(
                "retry_wait" if error.retryable else "failed",
                job.id,
            )
