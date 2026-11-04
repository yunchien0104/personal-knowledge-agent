"""Deep-only paid execution boundary around the existing R2A/R2E/R2F components."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import os
from typing import Any, Mapping
from uuid import UUID

from personal_brain.llm_client import LLMUsage, StructuredLLMClient, StructuredLLMResult
from personal_brain.research.contracts import (
    AtomicResearchDraft,
    ResearchClaim,
    VerificationResult,
)
from personal_brain.research.deep_llm import VerifiedPricing
from personal_brain.research.evidence import validate_atomic_citations
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
            if not 256 <= value <= 8192:
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
            synthesis_output_tokens=int(os.getenv("DEEP_R2A_MAX_OUTPUT_TOKENS", "2560")),
            verification_input_tokens=int(os.getenv("DEEP_R2E_MAX_INPUT_TOKENS", "4000")),
            verification_output_tokens=int(os.getenv("DEEP_R2E_MAX_OUTPUT_TOKENS", "768")),
            repair_input_tokens=int(os.getenv("DEEP_R2F_REPAIR_MAX_INPUT_TOKENS", "64000")),
            repair_output_tokens=int(os.getenv("DEEP_R2F_REPAIR_MAX_OUTPUT_TOKENS", "2560")),
            reverification_input_tokens=int(os.getenv("DEEP_R2F_VERIFY_MAX_INPUT_TOKENS", "4000")),
            reverification_output_tokens=int(os.getenv("DEEP_R2F_VERIFY_MAX_OUTPUT_TOKENS", "768")),
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
        timeout = int(os.getenv("DEEP_FINAL_TIMEOUT_SECONDS", "60"))
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
    ) -> FinalizationPreflight:
        synthesis_plan = _synthesis_plan(plan)
        synthesis_evidence = _deduplicated_evidence(evidence)
        synthesis_input = self.generator._synthesis_input(
            question,
            synthesis_plan,
            synthesis_evidence,
            max_claims=self.limits.max_claims,
        )
        input_upper_bound = _payload_token_upper_bound(
            synthesis_input,
            self.generator._synthesis_prompt(self.limits.max_claims),
        )
        if input_upper_bound > self.limits.synthesis_input_tokens:
            raise FinalizationPayloadLimitError(
                "R2A synthesis",
                observed_upper_bound=input_upper_bound,
                configured_limit=self.limits.synthesis_input_tokens,
            )
        slots: list[FinalizationSlot] = [FinalizationSlot(
            key="final:synthesize:v1",
            action_type="synthesize",
            cost_category="r2a",
            artifact_type="draft",
            slot_index=0,
            estimated_cost_usd=self._cost(
                self.limits.synthesis_input_tokens,
                self.limits.synthesis_output_tokens,
            ),
            parameters={"runId": str(run_id), "stage": "synthesis"},
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
    ) -> AtomicResearchDraft:
        try:
            draft = self.validate_draft(call.output)
            self.validate_citations(draft, evidence)
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
    ) -> AtomicResearchDraft:
        try:
            repaired = self.validate_draft(call.output)
            self.validate_repair(original, repaired, results)
            self.validate_citations(repaired, evidence)
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
        if self.judge.input_token_upper_bound(claim, evidence) > input_limit:
            raise FinalizationPayloadLimitError(
                "R2E verification payload exceeds its configured input limit"
            )
        call = self.judge.verify_with_metadata(claim, evidence)
        return call.result, call.transport

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
