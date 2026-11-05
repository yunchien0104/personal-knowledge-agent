"""Deep-only paid execution boundary around the existing R2A/R2E/R2F components."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import os
import re
from typing import Any, Mapping
from uuid import UUID

from personal_brain.llm_client import LLMUsage, StructuredLLMClient, StructuredLLMResult
from personal_brain.research.contracts import (
    AtomicResearchDraft,
    ResearchClaim,
    VerificationResult,
)
from personal_brain.research.deep_llm import VerifiedPricing
from personal_brain.research.evidence import (
    normalize_atomic_citation_quotes,
    validate_atomic_citations,
)
from personal_brain.research.gate import GateDecision, evaluate_gate
from personal_brain.research.llm import OpenAICompatibleResearchModel
from personal_brain.research.orchestrator import ResearchExecution, ResearchOrchestrator
from personal_brain.research.repair import evaluate_repair, validate_repaired_draft
from personal_brain.research.report_builder import build_verified_report
from personal_brain.research.verifier import LLMClaimJudge


@dataclass(frozen=True, slots=True)
class FinalizationLimits:
    max_claims: int
    synthesis_input_tokens: int
    synthesis_output_tokens: int
    verification_input_tokens: int
    verification_output_tokens: int
    repair_input_tokens: int
    repair_output_tokens: int
    reverification_input_tokens: int
    reverification_output_tokens: int

    def __post_init__(self) -> None:
        if not 1 <= self.max_claims <= 20:
            raise ValueError("DEEP_FINAL_MAX_CLAIMS must be between 1 and 20")
        for value in (
            self.synthesis_input_tokens,
            self.verification_input_tokens,
            self.repair_input_tokens,
            self.reverification_input_tokens,
        ):
            if not 256 <= value <= 200_000:
                raise ValueError("Deep finalization input limits are invalid")
        for value in (
            self.synthesis_output_tokens,
            self.verification_output_tokens,
            self.repair_output_tokens,
            self.reverification_output_tokens,
        ):
            if not 256 <= value <= 32_768:
                raise ValueError("Deep finalization output limits are invalid")
        if self.reverification_output_tokens != self.verification_output_tokens:
            raise ValueError(
                "R2E and R2F reverification output limits must match because they "
                "share one Deep-only verifier client"
            )

    @classmethod
    def from_environment(cls) -> "FinalizationLimits":
        return cls(
            max_claims=int(os.getenv("DEEP_FINAL_MAX_CLAIMS", "6")),
            synthesis_input_tokens=int(os.getenv("DEEP_R2A_MAX_INPUT_TOKENS", "48000")),
            synthesis_output_tokens=int(os.getenv("DEEP_R2A_MAX_OUTPUT_TOKENS", "16384")),
            verification_input_tokens=int(os.getenv("DEEP_R2E_MAX_INPUT_TOKENS", "4000")),
            verification_output_tokens=int(os.getenv("DEEP_R2E_MAX_OUTPUT_TOKENS", "4096")),
            repair_input_tokens=int(os.getenv("DEEP_R2F_REPAIR_MAX_INPUT_TOKENS", "64000")),
            repair_output_tokens=int(os.getenv("DEEP_R2F_REPAIR_MAX_OUTPUT_TOKENS", "16384")),
            reverification_input_tokens=int(os.getenv("DEEP_R2F_VERIFY_MAX_INPUT_TOKENS", "4000")),
            reverification_output_tokens=int(os.getenv("DEEP_R2F_VERIFY_MAX_OUTPUT_TOKENS", "4096")),
        )


@dataclass(frozen=True, slots=True)
class FinalizationSlot:
    key: str
    action_type: str
    cost_category: str
    artifact_type: str
    slot_index: int
    estimated_cost_usd: Decimal
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FinalizationPreflight:
    slots: tuple[FinalizationSlot, ...]
    maximum_cost_usd: Decimal
    synthesis_evidence: tuple[dict[str, Any], ...]
    payload_manifest: dict[str, Any]
    synthesis_input_upper_bound: int


class FinalizationPayloadLimitError(ValueError):
    """Safe payload-limit failure without retaining prompt or evidence text."""

    def __init__(
        self,
        stage: str,
        *,
        observed_upper_bound: int | None = None,
        configured_limit: int | None = None,
    ) -> None:
        self.stage = stage
        self.observed_upper_bound = observed_upper_bound
        self.configured_limit = configured_limit
        if observed_upper_bound is not None and configured_limit is not None:
            message = (
                f"{stage} payload token upper bound {observed_upper_bound} "
                f"exceeds configured input limit {configured_limit}"
            )
        else:
            message = stage
        super().__init__(message)


class FinalizationContractValidationError(ValueError):
    """Backend rejection after a paid response, with accounting metadata."""

    def __init__(self, message: str, call: StructuredLLMResult) -> None:
        super().__init__(message)
        self.call = call


class FinalizationProviderMetadataError(RuntimeError):
    def __init__(
        self,
        message: str,
        call: StructuredLLMResult,
        *,
        error_kind: str,
        outcome_unknown: bool = True,
    ) -> None:
        super().__init__(message)
        self.call = call
        self.error_kind = error_kind
        self.outcome_unknown = outcome_unknown


def _payload_token_upper_bound(value: Any, system_prompt: str = "") -> int:
    # Every tokenizer token represents at least one serialized byte. This byte
    # count is therefore a safe tokenizer-independent upper bound, not an exact
    # token count. It intentionally fails closed when no model tokenizer or
    # provider count-input endpoint is available offline.
    return len(json.dumps(
        {"system": system_prompt, "input": value},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode("utf-8"))


def _synthesis_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Remove execution-only plan metadata while retaining research semantics."""
    retained = (
        "planId",
        "planVersion",
        "normalizedResearchIntent",
        "objective",
        "explicitConstraints",
        "inferredAssumptions",
        "essentialClarifications",
        "subquestions",
        "evidenceRequirements",
        "coverageCriteria",
        "analysisSpec",
    )
    return {key: plan[key] for key in retained if key in plan}


def _deduplicated_evidence(
    evidence: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate exact Evidence IDs without weakening citation provenance."""
    rows: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for raw in evidence:
        row = dict(raw)
        evidence_id = str(row.get("id") or "").strip()
        if not evidence_id:
            raise ValueError("Finalization evidence is missing its immutable ID")
        prior = by_id.get(evidence_id)
        if prior is not None:
            if prior != row:
                raise ValueError("Conflicting duplicate Finalization evidence ID")
            continue
        by_id[evidence_id] = row
        rows.append(row)
    return rows


@dataclass(frozen=True, slots=True)
class _EvidenceSegment:
    start: int
    end: int
    text: str
    index: int


def _semantic_terms(value: Any) -> frozenset[str]:
    """Extract deterministic Latin tokens and CJK bigrams for payload ranking."""
    text = json.dumps(value, ensure_ascii=False, default=str).lower()
    terms = set(re.findall(r"[a-z0-9][a-z0-9_.+-]{1,}", text))
    for run in re.findall(r"[\u3400-\u9fff]{2,}", text):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return frozenset(terms)


def _split_exact_segments(text: str, *, maximum_bytes: int = 1200) -> list[_EvidenceSegment]:
    """Split without rewriting source text, so every selected quote remains auditable."""
    boundaries = [0]
    for match in re.finditer(r"(?:\r?\n)+|(?<=[。！？!?；;])", text):
        boundaries.append(match.end())
    if boundaries[-1] != len(text):
        boundaries.append(len(text))
    pieces = [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)]
    result: list[_EvidenceSegment] = []
    current_start: int | None = None
    current_end: int | None = None

    def append_bounded(start: int, end: int) -> None:
        cursor = start
        while cursor < end:
            stop = cursor
            size = 0
            while stop < end:
                encoded = text[stop].encode("utf-8")
                if size + len(encoded) > maximum_bytes and stop > cursor:
                    break
                size += len(encoded)
                stop += 1
            result.append(_EvidenceSegment(cursor, stop, text[cursor:stop], len(result)))
            cursor = stop

    for start, end in pieces:
        if not text[start:end]:
            continue
        candidate_start = start if current_start is None else current_start
        candidate_end = end
        if len(text[candidate_start:candidate_end].encode("utf-8")) <= maximum_bytes:
            current_start, current_end = candidate_start, candidate_end
            continue
        if current_start is not None and current_end is not None:
            append_bounded(current_start, current_end)
        current_start, current_end = None, None
        append_bounded(start, end)
    if current_start is not None and current_end is not None:
        append_bounded(current_start, current_end)
    return result


def _segment_score(segment: _EvidenceSegment, terms: frozenset[str]) -> int:
    lowered = segment.text.lower()
    overlap = sum(1 for term in terms if term in lowered)
    numeric = len(re.findall(r"(?:20\d{2}|\d+(?:\.\d+)?%|\d+(?:\.\d+)?[a-z]+)", lowered))
    caution = len(re.findall(r"風險|瓶頸|限制|不足|缺乏|延遲|矛盾|替代|假設|預測", segment.text))
    return overlap * 5 + min(numeric, 8) * 3 + min(caution, 5) * 4


def _payload_manifest_hash(value: Mapping[str, Any]) -> str:
    return sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()


def _shape_synthesis_evidence(
    *,
    question: str,
    plan: Mapping[str, Any],
    evidence: list[Mapping[str, Any]],
    assessment: Mapping[str, Any] | None,
    input_limit: int,
    generator: OpenAICompatibleResearchModel,
    max_claims: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    """Build a bounded, extractive R2A payload with an explicit audit manifest."""
    original = _deduplicated_evidence(evidence)
    compact_plan = _synthesis_plan(plan)
    prompt = generator._synthesis_prompt(max_claims)

    def upper(rows: list[dict[str, Any]]) -> int:
        return _payload_token_upper_bound(
            generator._synthesis_input(
                question, compact_plan, rows, max_claims=max_claims,
            ),
            prompt,
        )

    original_upper = upper(original)
    if original_upper <= input_limit:
        manifest = {
            "schemaVersion": "r2a-payload-manifest-v1",
            "algorithmVersion": "extractive-coverage-v1",
            "originalInputUpperBound": original_upper,
            "shapedInputUpperBound": original_upper,
            "configuredInputLimit": input_limit,
            "includedEvidenceIds": [str(row["id"]) for row in original],
            "excludedEvidence": [],
            "partiallyIncludedEvidence": [],
            "coverageImpact": [],
        }
        manifest["manifestHash"] = _payload_manifest_hash(manifest)
        return original, manifest, original_upper

    assessment_value = dict(assessment or {})
    requirements = {
        str(item.get("id")): item
        for item in plan.get("evidenceRequirements", [])
        if isinstance(item, Mapping) and item.get("required") is True
    }
    coverage_rows = [
        dict(item) for item in assessment_value.get("requirementCoverage", [])
        if isinstance(item, Mapping)
    ]
    quality = {
        str(item.get("evidenceId")): dict(item)
        for item in assessment_value.get("evidenceQuality", [])
        if isinstance(item, Mapping) and item.get("evidenceId")
    }
    conflict_ids = {
        str(evidence_id)
        for conflict in assessment_value.get("conflicts", [])
        if isinstance(conflict, Mapping)
        for evidence_id in conflict.get("evidenceIds", [])
    }
    contradiction_ids = {
        str(evidence_id)
        for item in coverage_rows
        for evidence_id in item.get("contradictingEvidenceIds", [])
    }
    rows_by_id = {str(row["id"]): row for row in original}
    segments = {
        evidence_id: _split_exact_segments(str(row.get("excerpt") or ""))
        for evidence_id, row in rows_by_id.items()
    }
    general_terms = _semantic_terms({"question": question, "plan": compact_plan})
    selected: dict[str, set[int]] = {evidence_id: set() for evidence_id in rows_by_id}
    reasons: dict[str, set[str]] = {evidence_id: set() for evidence_id in rows_by_id}
    coverage_selection: dict[str, set[str]] = {key: set() for key in requirements}

    def select_best(evidence_id: str, terms: frozenset[str], reason: str) -> None:
        candidates = segments.get(evidence_id) or []
        if not candidates:
            return
        best = max(candidates, key=lambda item: (
            _segment_score(item, terms), -item.index,
        ))
        selected[evidence_id].add(best.index)
        reasons[evidence_id].add(reason)

    # Preserve every source viewpoint and every immutable Evidence identity.
    for evidence_id in rows_by_id:
        select_best(evidence_id, general_terms, "source_viewpoint")

    # Required coverage and counter-evidence get mandatory, requirement-specific excerpts.
    for item in coverage_rows:
        requirement_id = str(item.get("requirementId") or "")
        if requirement_id not in requirements:
            continue
        relevant_ids = [str(value) for value in (
            *item.get("supportingEvidenceIds", []),
            *item.get("contradictingEvidenceIds", []),
        ) if str(value) in rows_by_id]
        requirement_terms = _semantic_terms(requirements[requirement_id])
        if relevant_ids:
            best_id = max(relevant_ids, key=lambda evidence_id: max(
                (_segment_score(segment, requirement_terms)
                 for segment in segments[evidence_id]),
                default=-1,
            ))
            select_best(best_id, requirement_terms, f"required_coverage:{requirement_id}")
            coverage_selection[requirement_id].add(best_id)
    for evidence_id in contradiction_ids | conflict_ids:
        if evidence_id in rows_by_id:
            select_best(evidence_id, general_terms, "contradiction_or_conflict")

    def shaped_rows() -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for evidence_id, source in rows_by_id.items():
            chosen = sorted(selected[evidence_id])
            is_partial = len(chosen) < len(segments[evidence_id])
            text = (
                "\n".join(segments[evidence_id][index].text for index in chosen)
                if is_partial else str(source["excerpt"])
            )
            row = {
                key: source[key]
                for key in ("id", "origin", "assetId", "revisionId", "locator")
                if key in source
            }
            row["excerpt"] = text
            if is_partial:
                row["payloadScope"] = {
                    "mode": "extractive_selection",
                    "omittedSourceText": True,
                    "claimsMustUseVisibleTextOnly": True,
                }
            output.append(row)
        return output

    mandatory_rows = shaped_rows()
    mandatory_upper = upper(mandatory_rows)
    if mandatory_upper > input_limit:
        raise FinalizationPayloadLimitError(
            "R2A synthesis",
            observed_upper_bound=mandatory_upper,
            configured_limit=input_limit,
        )

    candidates: list[tuple[int, int, str, int]] = []
    for evidence_id, items in segments.items():
        evidence_bonus = 0
        if evidence_id in contradiction_ids or evidence_id in conflict_ids:
            evidence_bonus += 60
        entry = quality.get(evidence_id, {})
        if entry.get("relevance") == "pass":
            evidence_bonus += 25
        if entry.get("reliability") == "pass":
            evidence_bonus += 15
        for segment in items:
            if segment.index in selected[evidence_id]:
                continue
            byte_size = max(1, len(segment.text.encode("utf-8")))
            score = _segment_score(segment, general_terms) + evidence_bonus
            candidates.append((score * 1000 // byte_size, score, evidence_id, segment.index))
    candidates.sort(reverse=True)
    for _, _, evidence_id, index in candidates:
        selected[evidence_id].add(index)
        candidate_rows = shaped_rows()
        if upper(candidate_rows) > input_limit:
            selected[evidence_id].remove(index)
        else:
            reasons[evidence_id].add("relevance_and_information_density")

    shaped = shaped_rows()
    shaped_upper = upper(shaped)
    evidence_manifest: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    shaped_by_id = {str(row["id"]): row for row in shaped}
    for evidence_id, source in rows_by_id.items():
        original_bytes = len(str(source["excerpt"]).encode("utf-8"))
        shaped_row = shaped_by_id[evidence_id]
        included_bytes = sum(
            len(segments[evidence_id][index].text.encode("utf-8"))
            for index in selected[evidence_id]
        )
        segment_ranges = [{
            "start": segments[evidence_id][index].start,
            "end": segments[evidence_id][index].end,
        } for index in sorted(selected[evidence_id])]
        item = {
            "evidenceId": evidence_id,
            "sourceExcerptHash": sha256(str(source["excerpt"]).encode("utf-8")).hexdigest(),
            "originalBytes": original_bytes,
            "includedBytes": included_bytes,
            "omittedBytes": original_bytes - included_bytes,
            "selectedCharacterRanges": segment_ranges,
            "selectionReasons": sorted(reasons[evidence_id]),
        }
        evidence_manifest.append(item)
        if item["omittedBytes"] > 0:
            partial.append(item)
    coverage_impact = []
    coverage_by_id = {
        str(item.get("requirementId")): item for item in coverage_rows
    }
    for requirement_id in requirements:
        assessed = coverage_by_id.get(requirement_id, {})
        referenced = [str(value) for value in (
            *assessed.get("supportingEvidenceIds", []),
            *assessed.get("contradictingEvidenceIds", []),
        )]
        represented = sorted(coverage_selection[requirement_id])
        coverage_impact.append({
            "requirementId": requirement_id,
            "assessmentStatus": assessed.get("status", "unknown"),
            "assessedEvidenceIds": referenced,
            "representedEvidenceIds": represented,
            "payloadCoverage": (
                "represented" if represented
                else "unresolved_no_assessed_evidence"
            ),
        })
    manifest = {
        "schemaVersion": "r2a-payload-manifest-v1",
        "algorithmVersion": "extractive-coverage-v1",
        "originalInputUpperBound": original_upper,
        "shapedInputUpperBound": shaped_upper,
        "configuredInputLimit": input_limit,
        "includedEvidenceIds": [str(row["id"]) for row in shaped],
        "excludedEvidence": [],
        "partiallyIncludedEvidence": partial,
        "evidenceAudit": evidence_manifest,
        "coverageImpact": coverage_impact,
    }
    manifest["manifestHash"] = _payload_manifest_hash(manifest)
    return shaped, manifest, shaped_upper


def _clone_client(
    client: StructuredLLMClient,
    *,
    max_output_tokens: int,
    timeout_seconds: int,
    reasoning_effort: str | None,
    service_tier: str,
    prompt_cache_mode: str,
) -> StructuredLLMClient:
    return StructuredLLMClient(
        client.base_url,
        client.api_key,
        client.model,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        provider=client.provider,
        reasoning_effort=reasoning_effort,
        service_tier=service_tier,
        prompt_cache_mode=prompt_cache_mode,
    )


class DeepFinalizationEngine:
    """Reuses Legacy R2 components but exposes every paid call as one durable slot."""

    def __init__(
        self,
        orchestrator: ResearchOrchestrator,
        pricing: VerifiedPricing,
        limits: FinalizationLimits,
        *,
        service_tier: str = "default",
    ) -> None:
        self.orchestrator = orchestrator
        self.generator = orchestrator.generator
        self.judge = orchestrator.judge
        self.pricing = pricing
        self.limits = limits
        self.service_tier = service_tier
        self.provider = self.generator.provider
        self.model_name = self.generator.model_name

    @classmethod
    def from_legacy_orchestrator(
        cls, orchestrator: ResearchOrchestrator
    ) -> "DeepFinalizationEngine":
        limits = FinalizationLimits.from_environment()
        pricing_model = os.getenv("DEEP_FINAL_PRICING_MODEL", "").strip()
        if pricing_model != orchestrator.generator.model_name:
            raise ValueError(
                "DEEP_FINAL_PRICING_MODEL must exactly match RESEARCH_MODEL"
            )
        pricing = VerifiedPricing.from_environment("DEEP_FINAL")
        timeout = int(os.getenv("DEEP_FINAL_TIMEOUT_SECONDS", "180"))
        reasoning = os.getenv("DEEP_FINAL_REASONING_EFFORT", "low").strip()
        service_tier = os.getenv("DEEP_FINAL_SERVICE_TIER", "default").strip()
        cache_mode = os.getenv("DEEP_FINAL_PROMPT_CACHE_MODE", "explicit").strip()
        base_client = orchestrator.generator.client
        generator = OpenAICompatibleResearchModel(
            _clone_client(
                base_client,
                max_output_tokens=limits.synthesis_output_tokens,
                timeout_seconds=timeout,
                reasoning_effort=reasoning,
                service_tier=service_tier,
                prompt_cache_mode=cache_mode,
            ),
            repair_client=_clone_client(
                base_client,
                max_output_tokens=limits.repair_output_tokens,
                timeout_seconds=timeout,
                reasoning_effort=reasoning,
                service_tier=service_tier,
                prompt_cache_mode=cache_mode,
            ),
        )
        judge = LLMClaimJudge(_clone_client(
            base_client,
            max_output_tokens=limits.verification_output_tokens,
            timeout_seconds=timeout,
            reasoning_effort=reasoning,
            service_tier=service_tier,
            prompt_cache_mode=cache_mode,
        ))
        return cls(
            ResearchOrchestrator(generator, judge),
            pricing,
            limits,
            service_tier=service_tier,
        )

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return self.pricing.cost(LLMUsage(input_tokens, output_tokens))

    def maximum_cost_usd(self, claim_count: int | None = None) -> Decimal:
        claims = self.limits.max_claims if claim_count is None else claim_count
        if not 1 <= claims <= self.limits.max_claims:
            raise ValueError("finalization claim count is outside the configured limit")
        return (
            self._cost(
                self.limits.synthesis_input_tokens,
                self.limits.synthesis_output_tokens,
            )
            + claims * self._cost(
                self.limits.verification_input_tokens,
                self.limits.verification_output_tokens,
            )
            + self._cost(
                self.limits.repair_input_tokens,
                self.limits.repair_output_tokens,
            )
            + claims * self._cost(
                self.limits.reverification_input_tokens,
                self.limits.reverification_output_tokens,
            )
        )

    def preflight(
        self,
        run_id: UUID,
        question: str,
        plan: Mapping[str, Any],
        evidence: list[Mapping[str, Any]],
        assessment: Mapping[str, Any] | None = None,
    ) -> FinalizationPreflight:
        synthesis_evidence, payload_manifest, input_upper_bound = (
            _shape_synthesis_evidence(
                question=question,
                plan=plan,
                evidence=evidence,
                assessment=assessment,
                input_limit=self.limits.synthesis_input_tokens,
                generator=self.generator,
                max_claims=self.limits.max_claims,
            )
        )
        slots: list[FinalizationSlot] = [FinalizationSlot(
            key="final:synthesize:v1",
            action_type="synthesize",
            cost_category="r2a",
            artifact_type="draft",
            slot_index=0,
            estimated_cost_usd=self._cost(
                input_upper_bound,
                self.limits.synthesis_output_tokens,
            ),
            parameters={
                "runId": str(run_id),
                "stage": "synthesis",
                "payloadManifestHash": payload_manifest["manifestHash"],
                "inputUpperBound": input_upper_bound,
            },
        )]
        for index in range(self.limits.max_claims):
            slots.append(FinalizationSlot(
                key=f"final:verify:{index}",
                action_type="verify",
                cost_category="r2e",
                artifact_type="verification",
                slot_index=index,
                estimated_cost_usd=self._cost(
                    self.limits.verification_input_tokens,
                    self.limits.verification_output_tokens,
                ),
                parameters={"runId": str(run_id), "stage": "verification", "slot": index},
            ))
        slots.append(FinalizationSlot(
            key="final:repair:v1",
            action_type="synthesize",
            cost_category="r2f",
            artifact_type="repaired_draft",
            slot_index=0,
            estimated_cost_usd=self._cost(
                self.limits.repair_input_tokens,
                self.limits.repair_output_tokens,
            ),
            parameters={"runId": str(run_id), "stage": "repair"},
        ))
        for index in range(self.limits.max_claims):
            slots.append(FinalizationSlot(
                key=f"final:reverify:{index}",
                action_type="verify",
                cost_category="r2f",
                artifact_type="reverification",
                slot_index=index,
                estimated_cost_usd=self._cost(
                    self.limits.reverification_input_tokens,
                    self.limits.reverification_output_tokens,
                ),
                parameters={"runId": str(run_id), "stage": "reverification", "slot": index},
            ))
        return FinalizationPreflight(
            tuple(slots),
            sum((slot.estimated_cost_usd for slot in slots), Decimal("0")),
            tuple(synthesis_evidence),
            payload_manifest,
            input_upper_bound,
        )

    def validate_draft(self, value: Mapping[str, Any]) -> AtomicResearchDraft:
        draft = AtomicResearchDraft.model_validate(value)
        claims = self.claims(draft)
        if len(claims) > self.limits.max_claims:
            raise FinalizationPayloadLimitError(
                "R2A produced more claims than the reserved verification slots"
            )
        return draft

    @staticmethod
    def validate_citations(
        draft: AtomicResearchDraft, evidence: list[Mapping[str, Any]]
    ) -> None:
        validate_atomic_citations(draft, evidence)

    @staticmethod
    def claims(draft: AtomicResearchDraft) -> list[ResearchClaim]:
        return [claim for finding in draft.findings for claim in finding.claims]

    def synthesis_call(
        self,
        question: str,
        plan: Mapping[str, Any],
        evidence: list[Mapping[str, Any]],
    ) -> StructuredLLMResult:
        return self.generator.synthesize_with_metadata(
            question,
            _synthesis_plan(plan),
            _deduplicated_evidence(evidence),
            max_claims=self.limits.max_claims,
        )

    def accept_synthesis_call(
        self,
        call: StructuredLLMResult,
        evidence: list[Mapping[str, Any]],
        *,
        original_evidence: list[Mapping[str, Any]] | None = None,
    ) -> AtomicResearchDraft:
        try:
            draft = self.validate_draft(call.output)
            draft = normalize_atomic_citation_quotes(
                draft, evidence, original_evidence
            )
            self.validate_citations(
                draft,
                original_evidence
                if original_evidence is not None
                else evidence,
            )
            return draft
        except (ValueError, KeyError) as error:
            raise FinalizationContractValidationError(
                "R2A output failed the Deep finalization contract",
                call,
            ) from error

    def repair_call(
        self,
        question: str,
        plan: Mapping[str, Any],
        evidence: list[Mapping[str, Any]],
        draft: AtomicResearchDraft,
        results: list[VerificationResult],
    ) -> StructuredLLMResult:
        repair_plan = _synthesis_plan(plan)
        repair_evidence = _deduplicated_evidence(evidence)
        payload = {
            "question": question,
            "plan": repair_plan,
            "evidence": repair_evidence,
            "previous_draft": draft.model_dump(),
            "verification_feedback": [item.model_dump() for item in results],
        }
        input_upper_bound = _payload_token_upper_bound(
            payload, self.generator._repair_prompt()
        )
        if input_upper_bound > self.limits.repair_input_tokens:
            raise FinalizationPayloadLimitError(
                "R2F repair",
                observed_upper_bound=input_upper_bound,
                configured_limit=self.limits.repair_input_tokens,
            )
        return self.generator.repair_with_metadata(
            question,
            repair_plan,
            repair_evidence,
            draft.model_dump(),
            [item.model_dump() for item in results],
        )

    def accept_repair_call(
        self,
        call: StructuredLLMResult,
        original: AtomicResearchDraft,
        results: list[VerificationResult],
        evidence: list[Mapping[str, Any]],
        *,
        original_evidence: list[Mapping[str, Any]] | None = None,
    ) -> AtomicResearchDraft:
        try:
            repaired = self.validate_draft(call.output)
            repaired = normalize_atomic_citation_quotes(
                repaired, evidence, original_evidence
            )
            self.validate_repair(original, repaired, results)
            self.validate_citations(
                repaired,
                original_evidence
                if original_evidence is not None
                else evidence,
            )
            return repaired
        except (ValueError, KeyError) as error:
            raise FinalizationContractValidationError(
                "R2F output failed the repair or citation contract",
                call,
            ) from error

    def verification_call(
        self,
        claim: ResearchClaim,
        evidence: list[Mapping[str, Any]],
        *,
        reverify: bool,
    ) -> tuple[VerificationResult, StructuredLLMResult | None]:
        input_limit = (
            self.limits.reverification_input_tokens
            if reverify else self.limits.verification_input_tokens
        )
        verification_evidence = self._verification_evidence(
            claim, evidence, input_limit
        )
        if self.judge.input_token_upper_bound(claim, verification_evidence) > input_limit:
            raise FinalizationPayloadLimitError(
                "R2E verification payload exceeds its configured input limit"
            )
        call = self.judge.verify_with_metadata(claim, verification_evidence)
        return call.result, call.transport

    def _verification_evidence(
        self,
        claim: ResearchClaim,
        evidence: list[Mapping[str, Any]],
        input_limit: int,
    ) -> list[dict[str, Any]]:
        """Keep every cited quote and grow exact context only while the guard allows."""
        rows = {str(row.get("id")): dict(row) for row in evidence}
        citations = {citation.evidenceId: citation.quote for citation in claim.citations}
        missing = sorted(set(citations) - set(rows))
        if missing:
            raise FinalizationPayloadLimitError(
                "R2E cited Evidence is absent from the shaped R2A payload"
            )

        positions: dict[str, tuple[int, int]] = {}
        for evidence_id, quote in citations.items():
            excerpt = str(rows[evidence_id].get("excerpt") or "")
            start = excerpt.find(quote)
            if start < 0:
                raise FinalizationPayloadLimitError(
                    "R2E cited quotation is absent from the shaped R2A payload"
                )
            positions[evidence_id] = (start, start + len(quote))

        def with_context(context_chars: int) -> list[dict[str, Any]]:
            selected: list[dict[str, Any]] = []
            for evidence_id in citations:
                source = rows[evidence_id]
                excerpt = str(source["excerpt"])
                start, end = positions[evidence_id]
                item = {
                    "id": evidence_id,
                    "excerpt": excerpt[
                        max(0, start - context_chars):min(len(excerpt), end + context_chars)
                    ],
                }
                if isinstance(source.get("locator"), Mapping):
                    item["locator"] = dict(source["locator"])
                selected.append(item)
            return selected

        minimal = with_context(0)
        if self.judge.input_token_upper_bound(claim, minimal) > input_limit:
            raise FinalizationPayloadLimitError(
                "R2E cited quotations exceed the configured input limit"
            )
        low, high = 0, max(
            (len(str(rows[evidence_id]["excerpt"])) for evidence_id in citations),
            default=0,
        )
        best = minimal
        while low <= high:
            middle = (low + high) // 2
            candidate = with_context(middle)
            if self.judge.input_token_upper_bound(claim, candidate) <= input_limit:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        return best

    def validate_transport(self, call: StructuredLLMResult) -> Decimal:
        if call.usage.input_tokens <= 0 or call.usage.output_tokens <= 0:
            raise FinalizationProviderMetadataError(
                "Finalization provider omitted authoritative usage",
                call,
                error_kind="missing_usage",
            )
        if call.service_tier != self.service_tier:
            raise FinalizationProviderMetadataError(
                "Finalization provider returned an unexpected service tier",
                call,
                error_kind="unexpected_service_tier",
            )
        if call.usage.cached_input_tokens or call.usage.cache_write_input_tokens:
            raise FinalizationProviderMetadataError(
                "Finalization provider used unpriced prompt caching",
                call,
                error_kind="unexpected_cache_usage",
            )
        return self.pricing.cost(call.usage)

    @staticmethod
    def gate(draft: AtomicResearchDraft, results: list[VerificationResult]) -> GateDecision:
        return evaluate_gate(
            results,
            [claim.claimId for claim in DeepFinalizationEngine.claims(draft)],
        )

    @staticmethod
    def should_repair(results: list[VerificationResult]) -> bool:
        return evaluate_repair(results, attempts_used=0, max_attempts=1).should_repair

    @staticmethod
    def validate_repair(
        original: AtomicResearchDraft,
        repaired: AtomicResearchDraft,
        results: list[VerificationResult],
    ) -> None:
        validate_repaired_draft(original=original, repaired=repaired, results=results)

    @staticmethod
    def execution(
        draft: AtomicResearchDraft,
        results: list[VerificationResult],
        gate: GateDecision,
        *,
        repaired: bool,
    ) -> ResearchExecution:
        return ResearchExecution(
            draft=draft,
            verification_results=results,
            gate=gate,
            verified_report=build_verified_report(draft, gate) if gate.can_publish else None,
            repair_attempts=1 if repaired else 0,
        )
