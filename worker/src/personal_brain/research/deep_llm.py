"""OpenAI-compatible, explicitly priced adapters for Deep R2B/R2C."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import json
import os
from typing import Any, Mapping
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from personal_brain.llm_client import (
    LLMUsage,
    StructuredLLMClient,
    StructuredLLMError,
)
from personal_brain.research.deep_evaluation import ProposedEvidenceAssessment
from personal_brain.research.deep_planning import ProposedResearchPlan
from personal_brain.research.schema_utils import to_openai_schema


MONEY_QUANTUM = Decimal("0.0001")
MAX_REPAIR_ATTEMPTS = 1
MAX_REPAIR_DIAGNOSTIC_CHARS = 1000


def _money(value: Decimal) -> Decimal:
    if value <= 0:
        return Decimal("0")
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING)


def bounded_repair_diagnostic(error: Exception) -> str:
    """Keep provider repair context printable and inside the reserved byte allowance."""
    printable = str(error).encode("ascii", errors="replace").decode("ascii")
    return " ".join(printable.split())[:MAX_REPAIR_DIAGNOSTIC_CHARS]


@dataclass(frozen=True, slots=True)
class VerifiedPricing:
    input_usd_per_million_tokens: Decimal
    output_usd_per_million_tokens: Decimal

    def __post_init__(self) -> None:
        if self.input_usd_per_million_tokens <= 0 or self.output_usd_per_million_tokens <= 0:
            raise ValueError("verified model pricing must be positive")

    def cost(self, usage: LLMUsage) -> Decimal:
        raw = (
            Decimal(usage.input_tokens) * self.input_usd_per_million_tokens
            + Decimal(usage.output_tokens) * self.output_usd_per_million_tokens
        ) / Decimal(1_000_000)
        return _money(raw)

    @classmethod
    def from_environment(cls, prefix: str) -> "VerifiedPricing":
        if os.environ.get(f"{prefix}_PRICING_VERIFIED", "").strip().lower() != "true":
            raise ValueError(f"{prefix}_PRICING_VERIFIED must be true")
        try:
            input_price = Decimal(os.environ[f"{prefix}_INPUT_COST_PER_MILLION_USD"])
            output_price = Decimal(os.environ[f"{prefix}_OUTPUT_COST_PER_MILLION_USD"])
        except (KeyError, ValueError) as error:
            raise ValueError(f"{prefix} verified pricing is incomplete") from error
        return cls(input_price, output_price)


@dataclass(frozen=True, slots=True)
class DeepModelCall:
    output: dict[str, Any]
    usage: LLMUsage
    cost_usd: Decimal
    latency_ms: int
    request_id: str | None
    provider: str
    model_name: str
    service_tier: str | None = None


class DeepModelValidationError(ValueError):
    def __init__(self, message: str, call: DeepModelCall) -> None:
        super().__init__(message)
        self.call = call


class DeepProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_kind: str,
        outcome_unknown: bool,
        usage: LLMUsage,
        known_cost_usd: Decimal,
        latency_ms: int,
        request_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.outcome_unknown = outcome_unknown
        self.usage = usage
        self.known_cost_usd = known_cost_usd
        self.latency_ms = latency_ms
        self.request_ids = request_ids

    def with_previous(
        self,
        *,
        usage: LLMUsage,
        cost_usd: Decimal,
        latency_ms: int,
        request_ids: tuple[str, ...],
    ) -> "DeepProviderError":
        return DeepProviderError(
            str(self),
            error_kind=self.error_kind,
            outcome_unknown=self.outcome_unknown,
            usage=usage + self.usage,
            known_cost_usd=cost_usd + self.known_cost_usd,
            latency_ms=latency_ms + self.latency_ms,
            request_ids=(*request_ids, *self.request_ids),
        )


class _StructuredDeepModel:
    def __init__(
        self,
        client: StructuredLLMClient,
        pricing: VerifiedPricing,
        output_model: type[BaseModel],
    ) -> None:
        self.client = client
        self.pricing = pricing
        self.output_model = output_model
        self.provider = client.provider
        self.model_name = client.model

    def max_operation_cost(self, prompt: Mapping[str, Any]) -> Decimal:
        system = str(prompt.get("systemInstructions") or "")
        user = {key: value for key, value in prompt.items() if key != "systemInstructions"}
        input_bytes = len(json.dumps(
            {"system": system, "user": user}, ensure_ascii=False, default=str,
        ).encode("utf-8"))
        # A tokenizer cannot produce more tokens than the UTF-8 byte count. Add
        # bounded repair diagnostics and reserve both the initial and repair calls.
        repair_input_upper = input_bytes + 4096
        input_tokens_upper = input_bytes + repair_input_upper
        output_tokens_upper = self.client.max_output_tokens * (1 + MAX_REPAIR_ATTEMPTS)
        return self.pricing.cost(LLMUsage(input_tokens_upper, output_tokens_upper))

    def _call(self, prompt: Mapping[str, Any]) -> DeepModelCall:
        system = prompt.get("systemInstructions")
        if not isinstance(system, str) or not system.strip():
            raise ValueError("Deep model system instructions are unavailable")
        user_input = {
            key: value for key, value in prompt.items()
            if key not in {"systemInstructions", "outputSchema"}
        }
        try:
            result = self.client.generate_with_metadata(
                system_prompt=system,
                user_input=user_input,
                output_schema=to_openai_schema(
                    self.output_model, preserve_numeric_bounds=True
                ),
            )
        except StructuredLLMError as error:
            request_ids = (error.request_id,) if error.request_id else ()
            raise DeepProviderError(
                str(error),
                error_kind=error.error_kind,
                outcome_unknown=error.outcome_unknown,
                usage=error.usage,
                known_cost_usd=self.pricing.cost(error.usage),
                latency_ms=error.latency_ms,
                request_ids=request_ids,
            ) from error
        if result.usage.input_tokens <= 0 or result.usage.output_tokens <= 0:
            request_ids = (result.request_id,) if result.request_id else ()
            raise DeepProviderError(
                "LLM provider omitted authoritative token usage",
                error_kind="missing_usage",
                outcome_unknown=True,
                usage=result.usage,
                known_cost_usd=Decimal("0"),
                latency_ms=result.latency_ms,
                request_ids=request_ids,
            )
        requested_tier = getattr(self.client, "service_tier", None)
        if requested_tier is not None and result.service_tier != requested_tier:
            request_ids = (result.request_id,) if result.request_id else ()
            raise DeepProviderError(
                "LLM provider did not use the requested Standard service tier",
                error_kind="unexpected_service_tier",
                outcome_unknown=True,
                usage=result.usage,
                known_cost_usd=Decimal("0"),
                latency_ms=result.latency_ms,
                request_ids=request_ids,
            )
        if result.usage.cached_input_tokens or result.usage.cache_write_input_tokens:
            request_ids = (result.request_id,) if result.request_id else ()
            raise DeepProviderError(
                "LLM provider used prompt caching despite explicit-only mode",
                error_kind="unexpected_cache_usage",
                outcome_unknown=True,
                usage=result.usage,
                known_cost_usd=Decimal("0"),
                latency_ms=result.latency_ms,
                request_ids=request_ids,
            )
        call = DeepModelCall(
            output=result.output,
            usage=result.usage,
            cost_usd=self.pricing.cost(result.usage),
            latency_ms=result.latency_ms,
            request_id=result.request_id,
            provider=result.provider,
            model_name=result.model,
            service_tier=result.service_tier,
        )
        try:
            validated = self.output_model.model_validate(call.output)
        except ValidationError as error:
            raise DeepModelValidationError(str(error)[:2000], call) from error
        return DeepModelCall(
            output=validated.model_dump(mode="json"),
            usage=call.usage,
            cost_usd=call.cost_usd,
            latency_ms=call.latency_ms,
            request_id=call.request_id,
            provider=call.provider,
            model_name=call.model_name,
            service_tier=call.service_tier,
        )


class OpenAICompatibleDeepPlanningModel(_StructuredDeepModel):
    def __init__(self, client: StructuredLLMClient, pricing: VerifiedPricing) -> None:
        super().__init__(client, pricing, ProposedResearchPlan)

    def propose(self, prompt: Mapping[str, Any]) -> DeepModelCall:
        return self._call(prompt)

    def repair(self, prompt: Mapping[str, Any]) -> DeepModelCall:
        return self._call(prompt)

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleDeepPlanningModel":
        return cls(_client_from_environment("R2B"), VerifiedPricing.from_environment("R2B"))


class OpenAICompatibleEvidenceEvaluationModel(_StructuredDeepModel):
    def __init__(self, client: StructuredLLMClient, pricing: VerifiedPricing) -> None:
        super().__init__(client, pricing, ProposedEvidenceAssessment)

    def evaluate(self, prompt: Mapping[str, Any]) -> DeepModelCall:
        return self._call(prompt)

    def repair(self, prompt: Mapping[str, Any]) -> DeepModelCall:
        return self._call(prompt)

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleEvidenceEvaluationModel":
        return cls(_client_from_environment("R2C"), VerifiedPricing.from_environment("R2C"))


def _client_from_environment(prefix: str) -> StructuredLLMClient:
    base_url = os.environ.get(
        "R2B_R2C_API_BASE_URL", os.environ.get("LLM_API_BASE_URL", "")
    ).strip()
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("R2B_R2C_API_BASE_URL must be a trusted HTTPS origin")
    api_key = os.environ.get("R2B_R2C_API_KEY", "").strip()
    model = os.environ.get(f"{prefix}_MODEL", "").strip()
    provider = os.environ.get("R2B_R2C_PROVIDER", "openai-compatible").strip()
    reasoning_effort = os.environ.get(f"{prefix}_REASONING_EFFORT", "low").strip()
    service_tier = os.environ.get("R2B_R2C_SERVICE_TIER", "default").strip()
    prompt_cache_mode = os.environ.get(
        "R2B_R2C_PROMPT_CACHE_MODE", "explicit"
    ).strip()
    if not api_key or not model or not provider:
        raise ValueError(f"{prefix} provider configuration is incomplete")
    return StructuredLLMClient(
        base_url,
        api_key,
        model,
        max_output_tokens=int(os.environ.get(f"{prefix}_MAX_OUTPUT_TOKENS", "2048")),
        timeout_seconds=int(os.environ.get(f"{prefix}_TIMEOUT_SECONDS", "60")),
        provider=provider,
        reasoning_effort=reasoning_effort,
        service_tier=service_tier,
        prompt_cache_mode=prompt_cache_mode,
    )
