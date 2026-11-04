import json
from dataclasses import dataclass
import socket
import time
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


@dataclass(frozen=True, slots=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_tokens: int = 0

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.total_tokens + other.total_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
            self.cache_write_input_tokens + other.cache_write_input_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
        )


@dataclass(frozen=True, slots=True)
class StructuredLLMResult:
    output: dict[str, Any]
    provider: str
    model: str
    usage: LLMUsage
    latency_ms: int
    request_id: str | None
    service_tier: str | None = None
    finish_reason: str | None = None


class StructuredLLMError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_kind: str = "provider_error",
        outcome_unknown: bool,
        usage: LLMUsage = LLMUsage(),
        latency_ms: int = 0,
        request_id: str | None = None,
        finish_reason: str | None = None,
        refusal: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.outcome_unknown = outcome_unknown
        self.usage = usage
        self.latency_ms = latency_ms
        self.request_id = request_id
        self.finish_reason = finish_reason
        self.refusal = refusal


def _usage(raw: Any) -> LLMUsage:
    value = raw if isinstance(raw, dict) else {}
    input_tokens = value.get("prompt_tokens", value.get("input_tokens", 0))
    output_tokens = value.get("completion_tokens", value.get("output_tokens", 0))
    total_tokens = value.get("total_tokens", 0)
    input_details = value.get("prompt_tokens_details", value.get("input_tokens_details", {}))
    output_details = value.get(
        "completion_tokens_details", value.get("output_tokens_details", {})
    )
    input_details = input_details if isinstance(input_details, dict) else {}
    output_details = output_details if isinstance(output_details, dict) else {}
    cached_input_tokens = input_details.get("cached_tokens", 0)
    cache_write_input_tokens = input_details.get("cache_write_tokens", 0)
    reasoning_tokens = output_details.get("reasoning_tokens", 0)
    if not all(isinstance(item, int) and item >= 0 for item in (
        input_tokens, output_tokens, total_tokens, cached_input_tokens,
        cache_write_input_tokens, reasoning_tokens,
    )):
        raise ValueError("LLM usage is invalid")
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    if total_tokens < input_tokens + output_tokens:
        raise ValueError("LLM total token usage is inconsistent")
    if cached_input_tokens + cache_write_input_tokens > input_tokens:
        raise ValueError("LLM input token details are inconsistent")
    if reasoning_tokens > output_tokens:
        raise ValueError("LLM output token details are inconsistent")
    return LLMUsage(
        input_tokens, output_tokens, total_tokens,
        cached_input_tokens, cache_write_input_tokens, reasoning_tokens,
    )


class StructuredLLMClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 *, max_output_tokens: int = 4096, timeout_seconds: int = 60,
                 provider: str = "openai-compatible",
                 reasoning_effort: str | None = None,
                 service_tier: str | None = None,
                 prompt_cache_mode: str | None = None):
        if not 256 <= max_output_tokens <= 8192:
            raise ValueError("max_output_tokens must be between 256 and 8192")
        if not 1 <= timeout_seconds <= 90:
            raise ValueError("timeout_seconds must be between 1 and 90")
        if reasoning_effort not in {None, "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("reasoning_effort is unsupported")
        if service_tier not in {None, "default"}:
            raise ValueError("only the Standard service tier is supported")
        if prompt_cache_mode not in {None, "explicit"}:
            raise ValueError("prompt_cache_mode must be explicit when configured")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.provider = provider
        self.reasoning_effort = reasoning_effort
        self.service_tier = service_tier
        self.prompt_cache_mode = prompt_cache_mode

    def generate(
        self,
        system_prompt: str,
        user_input: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:

        return self.generate_with_metadata(
            system_prompt=system_prompt,
            user_input=user_input,
            output_schema=output_schema,
        ).output

    def generate_with_metadata(
        self,
        system_prompt: str,
        user_input: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> StructuredLLMResult:
        payload = {
            "model": self.model,
            "max_completion_tokens": self.max_output_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_input, ensure_ascii=False),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "research_output",
                    "strict": True,
                    "schema": output_schema,
                },
            },
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.service_tier is not None:
            payload["service_tier"] = self.service_tier
        if self.prompt_cache_mode is not None:
            payload["prompt_cache_options"] = {"mode": self.prompt_cache_mode}

        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        started = time.monotonic()
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read()
                header_request_id = response.headers.get("x-request-id")
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            parsed_usage = LLMUsage()
            try:
                error_result = json.loads(detail)
                parsed_usage = _usage(error_result.get("usage"))
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                pass
            rejected_before_execution = error.code in {400, 401, 403, 404, 422}
            raise StructuredLLMError(
                f"LLM HTTP {error.code}: {detail[:2000]}",
                error_kind=(
                    "provider_rejection" if rejected_before_execution
                    else "provider_http_error"
                ),
                outcome_unknown=not rejected_before_execution,
                usage=parsed_usage,
                latency_ms=int((time.monotonic() - started) * 1000),
                request_id=(error.headers.get("x-request-id") if error.headers else None),
            ) from error

        except (URLError, TimeoutError) as error:
            timed_out = isinstance(error, TimeoutError) or (
                isinstance(error, URLError)
                and isinstance(getattr(error, "reason", None), (TimeoutError, socket.timeout))
            )
            raise StructuredLLMError(
                "LLM request timed out" if timed_out else "LLM connection failed",
                error_kind="timeout" if timed_out else "transport_error",
                outcome_unknown=True,
                latency_ms=int((time.monotonic() - started) * 1000),
            ) from error

        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            result = json.loads(response_body)
            usage = _usage(result.get("usage"))
            choice = result["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason")
            refusal = message.get("refusal")
            model = result.get("model") or self.model
            request_id = header_request_id or result.get("id")
            service_tier = result.get("service_tier")
            if not isinstance(model, str) or not model:
                raise ValueError("LLM model metadata is invalid")
            if request_id is not None and not isinstance(request_id, str):
                raise ValueError("LLM request ID is invalid")
            if service_tier is not None and not isinstance(service_tier, str):
                raise ValueError("LLM service tier metadata is invalid")
            if finish_reason is not None and not isinstance(finish_reason, str):
                raise ValueError("LLM finish reason metadata is invalid")
            if refusal is not None and not isinstance(refusal, str):
                raise ValueError("LLM refusal metadata is invalid")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            parsed_usage = usage if "usage" in locals() else LLMUsage()
            raise StructuredLLMError(
                "LLM structured response is invalid",
                error_kind="invalid_provider_response",
                outcome_unknown=False,
                usage=parsed_usage,
                latency_ms=latency_ms,
                request_id=header_request_id or (
                    result.get("id")
                    if isinstance(locals().get("result"), dict) else None
                ),
            ) from error

        terminal_errors = {
            "length": (
                "LLM output token budget was exhausted",
                "output_budget_exhausted",
            ),
            "content_filter": (
                "LLM response was blocked by the content filter",
                "content_filter",
            ),
        }
        if finish_reason in terminal_errors:
            message_text, error_kind = terminal_errors[finish_reason]
            raise StructuredLLMError(
                message_text,
                error_kind=error_kind,
                outcome_unknown=False,
                usage=usage,
                latency_ms=latency_ms,
                request_id=request_id,
                finish_reason=finish_reason,
                refusal=refusal,
            )
        if refusal:
            raise StructuredLLMError(
                "LLM refused the structured request",
                error_kind="provider_refusal",
                outcome_unknown=False,
                usage=usage,
                latency_ms=latency_ms,
                request_id=request_id,
                finish_reason=finish_reason,
                refusal=refusal,
            )
        if finish_reason not in {None, "stop"}:
            raise StructuredLLMError(
                "LLM stopped without a structured response",
                error_kind="unexpected_finish_reason",
                outcome_unknown=False,
                usage=usage,
                latency_ms=latency_ms,
                request_id=request_id,
                finish_reason=finish_reason,
            )

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise StructuredLLMError(
                "LLM returned no structured response content",
                error_kind="empty_provider_response",
                outcome_unknown=False,
                usage=usage,
                latency_ms=latency_ms,
                request_id=request_id,
                finish_reason=finish_reason,
            )
        try:
            output = json.loads(content)
            if not isinstance(output, dict):
                raise ValueError("structured LLM output must be an object")
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise StructuredLLMError(
                "LLM structured response JSON is invalid",
                error_kind="invalid_json_response",
                outcome_unknown=False,
                usage=usage,
                latency_ms=latency_ms,
                request_id=request_id,
                finish_reason=finish_reason,
            ) from error
        return StructuredLLMResult(
            output=output,
            provider=self.provider,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            request_id=request_id,
            service_tier=service_tier,
            finish_reason=finish_reason,
        )
