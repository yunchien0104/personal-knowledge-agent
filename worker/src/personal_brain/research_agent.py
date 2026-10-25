from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import logging
import time
from typing import Any, Mapping, Protocol
from uuid import UUID

from personal_brain.queue_runtime import LeaseGuard, LeaseLostError, ProcessResult
from personal_brain.query_planning import ProductionQueryPlanner
from personal_brain.source_notes import ModelError, _text
from personal_brain.supabase_gateway import ClaimedJob, GatewayError
from personal_brain.research.orchestrator import ResearchOrchestrator
from pydantic import ValidationError

PROMPT_VERSION = "research-internal-v1"
MAX_EVIDENCE = 12
HYBRID_CANDIDATE_LIMIT = 40
RRF_K = 60
R2_PROMPT_VERSION = "research-r2-v1"
LOGGER = logging.getLogger("personal_brain.research")


def _log_stage(job: ClaimedJob, stage: str, started_at: float, **fields: Any) -> None:
    LOGGER.info(json.dumps({
        "event": "research_stage",
        "job_id": job.id,
        "user_id": getattr(job, "user_id", None),
        "stage": stage,
        "latency_ms": round((time.monotonic() - started_at) * 1000, 2),
        **fields,
    }, separators=(",", ":"), sort_keys=True))


class StructuredResearchModel(Protocol):
    provider: str
    model_name: str

    def plan(self, question: str) -> Mapping[str, Any]: ...

    def synthesize(
        self, question: str, plan: Mapping[str, Any], evidence: list[Mapping[str, Any]]
    ) -> Mapping[str, Any]: ...


class ResearchRetrieval(Protocol):
    def retrieve(
        self, user_id: str, query: str, *, scope_asset_ids: list[str] | None,
        top_k: int, candidate_limit: int,
    ) -> Mapping[str, Any]: ...


class LocalExtractiveResearchModel:
    """Zero-cost fallback synthesis plus the production deterministic planner."""

    provider = "local"
    model_name = "extractive-research-v1"

    def plan(self, question: str) -> Mapping[str, Any]:
        return ProductionQueryPlanner().plan(question)

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
    required = {
        "question", "scope", "subquestions", "evidenceRequirements",
        "maxCostUsd", "maxQueries", "maxRounds", "stopConditions",
    }
    allowed = required | {
        "normalizedQuestion", "intent", "lexicalQueries", "semanticQueries", "searchKeywords",
    }
    if not required.issubset(output) or not set(output).issubset(allowed) or output.get("question") != question:
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
    validated = {
        "question": question,
        "scope": scope,
        "subquestions": [_text(value, maximum=200) for value in subquestions],
        "evidenceRequirements": [_text(value, maximum=1000) for value in requirements],
        "maxCostUsd": 0,
        "maxQueries": output["maxQueries"],
        "maxRounds": 1,
        "stopConditions": [_text(value, maximum=1000) for value in stop_conditions],
    }
    for key in ("normalizedQuestion", "intent"):
        if key in output:
            validated[key] = _text(output.get(key), maximum=500)
    for key in ("lexicalQueries", "semanticQueries", "searchKeywords"):
        if key in output:
            values = output.get(key)
            if not isinstance(values, list) or not 1 <= len(values) <= 24:
                raise ModelError("invalid_research_plan", "Research plan is invalid", retryable=False)
            validated[key] = [_text(value, maximum=500) for value in values]
    return validated


def research_asset_scope(job: ClaimedJob) -> list[str] | None:
    raw_scope = job.payload.input.get("scopeAssetIds")
    if raw_scope is None:
        return None
    if not isinstance(raw_scope, list) or not 1 <= len(raw_scope) <= 100:
        raise GatewayError("invalid_research_scope", "Research Asset scope is invalid", retryable=False)
    scope: list[str] = []
    seen: set[str] = set()
    for value in raw_scope:
        if not isinstance(value, str):
            raise GatewayError("invalid_research_scope", "Research Asset scope is invalid", retryable=False)
        try:
            canonical = str(UUID(value))
        except ValueError as error:
            raise GatewayError("invalid_research_scope", "Research Asset scope is invalid", retryable=False) from error
        if canonical != value.lower() or canonical in seen:
            raise GatewayError("invalid_research_scope", "Research Asset scope is invalid", retryable=False)
        seen.add(canonical)
        scope.append(canonical)
    return scope


def _validate_retrieval_result(result: Mapping[str, Any]) -> tuple[str, list[Mapping[str, Any]]]:
    semantic_status = result.get("semanticStatus")
    rows = result.get("results")
    if semantic_status not in {"ready", "degraded_embedding_unavailable"} or not isinstance(rows, list):
        raise ModelError("invalid_research_context", "Hybrid retrieval context is invalid", retryable=False)
    for row in rows:
        if not isinstance(row, Mapping):
            raise ModelError("invalid_research_context", "Hybrid retrieval context is invalid", retryable=False)
        method = row.get("retrievalMethod")
        if method not in {"lexical", "semantic", "hybrid"}:
            raise ModelError("invalid_research_context", "Hybrid retrieval method is invalid", retryable=False)
        if semantic_status == "degraded_embedding_unavailable" and method != "lexical":
            raise ModelError("invalid_research_context", "Degraded retrieval contains semantic results", retryable=False)
    return semantic_status, rows


def retrieve_planned_evidence(
    retrieval: ResearchRetrieval,
    user_id: str,
    plan: Mapping[str, Any],
    scope_asset_ids: list[str] | None,
    *,
    job: ClaimedJob | None = None,
    worker_id: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    semantic_queries = plan.get("semanticQueries")
    lexical_queries = plan.get("lexicalQueries")
    raw_queries: list[Any] = []
    if isinstance(semantic_queries, list) and semantic_queries:
        raw_queries.append(semantic_queries[0])
    if isinstance(lexical_queries, list):
        raw_queries.extend(lexical_queries[1:2])
    if isinstance(semantic_queries, list):
        raw_queries.extend(semantic_queries[1:])
    if isinstance(lexical_queries, list):
        raw_queries.extend(lexical_queries)
    if not raw_queries:
        raw_queries = [plan["question"]]
    queries: list[str] = []
    for value in raw_queries:
        if isinstance(value, str) and value.strip() and value not in queries:
            queries.append(value)
        if len(queries) == int(plan["maxQueries"]):
            break

    fused: dict[str, dict[str, Any]] = {}
    degraded = False
    for query in queries:
        job_retrieval = getattr(retrieval, "retrieve_for_job", None)
        if callable(job_retrieval) and job is not None and worker_id is not None:
            result = job_retrieval(
                job, worker_id, query, scope_asset_ids=scope_asset_ids,
                top_k=MAX_EVIDENCE, candidate_limit=HYBRID_CANDIDATE_LIMIT,
            )
        else:
            result = retrieval.retrieve(
                user_id, query, scope_asset_ids=scope_asset_ids,
                top_k=MAX_EVIDENCE, candidate_limit=HYBRID_CANDIDATE_LIMIT,
            )
        semantic_status, rows = _validate_retrieval_result(result)
        degraded = degraded or semantic_status != "ready"
        for rank, row in enumerate(rows, start=1):
            chunk_id = row.get("chunkId")
            if not isinstance(chunk_id, str):
                chunk_id = f"{row.get('evidenceId')}:{row.get('startOffset', 0)}"
            entry = fused.setdefault(chunk_id, {"row": row, "score": 0.0, "queryHits": 0})
            entry["score"] += 1.0 / (RRF_K + rank)
            entry["queryHits"] += 1

    evidence_by_id: dict[str, dict[str, Any]] = {}
    for entry in sorted(fused.values(), key=lambda item: (-item["score"], str(item["row"].get("chunkId", "")))):
        row = entry["row"]
        evidence_id = row.get("evidenceId")
        asset_id = row.get("assetId")
        revision_id = row.get("revisionId")
        content = row.get("content")
        locator = row.get("locator")
        if not all(isinstance(value, str) for value in (evidence_id, asset_id, revision_id, content)) or not content.strip() or not isinstance(locator, Mapping):
            raise ModelError("invalid_research_context", "Hybrid retrieval result is invalid", retryable=False)
        try:
            if any(str(UUID(value)) != value.lower() for value in (evidence_id, asset_id, revision_id)):
                raise ValueError
        except ValueError as error:
            raise ModelError("invalid_research_context", "Hybrid retrieval provenance is invalid", retryable=False) from error
        previous = evidence_by_id.get(evidence_id)
        if previous is None or entry["score"] > previous["retrievalScore"]:
            evidence_by_id[evidence_id] = {
                "id": evidence_id,
                "assetId": asset_id,
                "revisionId": revision_id,
                "excerpt": content,
                "locator": dict(locator),
                "retrievalMethod": row.get("retrievalMethod"),
                "retrievalStatus": "degraded_embedding_unavailable" if degraded else "ready",
                "retrievalScore": entry["score"],
                "queryHits": entry["queryHits"],
            }
    selected = sorted(
        evidence_by_id.values(),
        key=lambda row: (-float(row["retrievalScore"]), -int(row["queryHits"]), row["id"]),
    )[:MAX_EVIDENCE]
    return selected, "degraded_embedding_unavailable" if degraded else "ready"


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
        retrieval_or_worker_id: ResearchRetrieval | str,
        worker_id: str | None = None,
        *,
        orchestrator: ResearchOrchestrator | None = None,
        lease_seconds: int = 120,
    ) -> None:
        self.gateway = gateway
        self.model = model
        if isinstance(retrieval_or_worker_id, str):
            self.retrieval: ResearchRetrieval | None = None
            self.worker_id = retrieval_or_worker_id
        else:
            if worker_id is None:
                raise ValueError("worker_id is required when retrieval is provided")
            self.retrieval = retrieval_or_worker_id
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
                stage_started = time.monotonic()
                plan = validate_plan(
                    self.model.plan(question), question
                )
                _log_stage(job, "planning", stage_started, query_count=plan["maxQueries"])
                lease.pulse()

                # 2. R1 Retrieval
                stage_started = time.monotonic()
                context = self.gateway.research_context(
                    job, self.worker_id, plan
                )
                if context is None:
                    raise LeaseLostError(
                        "Worker lease is no longer valid"
                    )

                if self.retrieval is None:
                    evidence = select_evidence(context)
                    retrieval_status = "legacy_context"
                else:
                    scope_asset_ids = research_asset_scope(job)
                    retrieved, retrieval_status = retrieve_planned_evidence(
                        self.retrieval, job.user_id, plan, scope_asset_ids,
                        job=job, worker_id=self.worker_id,
                    )
                    evidence = select_evidence({"evidence": retrieved})
                    recorder = getattr(self.gateway, "record_research_retrieval", None)
                    if callable(recorder):
                        recorded = recorder(
                            job, self.worker_id, plan, evidence, retrieval_status
                        )
                        if recorded is None:
                            raise LeaseLostError("Worker lease expired before retrieval checkpoint")
                _log_stage(
                    job, "retrieval", stage_started,
                    evidence_count=len(evidence), retrieval_status=retrieval_status,
                )
                lease.pulse()

                # 3. R2 Generate -> Verify -> Repair -> Gate
                stage_started = time.monotonic()
                if self.orchestrator is None:
                    output = self.model.synthesize(question, plan, evidence)
                    draft = validate_synthesis(output, evidence)
                    execution = None
                else:
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

                _log_stage(job, "r2", stage_started)
                lease.pulse()

                # 4. Verification Gate
                if execution is not None and not execution.gate.can_publish:
                    raise ModelError(
                        "research_verification_failed",
                        "Research claims did not pass verification",
                        retryable=False,
                    )

                # 5. Convert verified claims to publication format
                if execution is not None:
                    try:
                        draft = adapt_r2_publication(execution, evidence)
                    except (ValueError, ModelError) as error:
                        raise ModelError(
                            "r2_publication_invalid",
                            "R2 publication draft is invalid",
                            retryable=False,
                        ) from error

                lease.pulse()

                # 6. Supabase Publication
                stage_started = time.monotonic()
                publication = self.gateway.publish_research_draft(
                    job,
                    self.worker_id,
                    model_provider=(self.orchestrator.generator.provider if self.orchestrator else self.model.provider),
                    model_name=(self.orchestrator.generator.model_name if self.orchestrator else self.model.model_name),
                    prompt_version=R2_PROMPT_VERSION if self.orchestrator else PROMPT_VERSION,
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

                _log_stage(job, "publication", stage_started)

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
