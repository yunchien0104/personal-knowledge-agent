import json
from io import BytesIO
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import pytest

from personal_brain.llm_client import StructuredLLMClient, StructuredLLMError


class FakeResponse:
    def __init__(self, body, request_id="req-header"):
        self.body = json.dumps(body).encode()
        self.headers = {"x-request-id": request_id}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def response():
    return {
        "id": "req-body",
        "model": "provider-model-version",
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"answer": "ok"}), "refusal": None},
        }],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def client():
    return StructuredLLMClient(
        "https://llm.example.test/v1", "secret", "configured-model",
        max_output_tokens=256, timeout_seconds=12, provider="fixture-provider",
    )


def configured_reasoning_client():
    return StructuredLLMClient(
        "https://api.openai.com/v1", "secret", "gpt-6.1-sol",
        max_output_tokens=1024, timeout_seconds=90, provider="openai",
        reasoning_effort="low", service_tier="default", prompt_cache_mode="explicit",
    )


def test_transport_accepts_audited_finalization_headroom():
    value = response()
    captured = {}

    def fake_urlopen(request, **kwargs):
        captured.update(json.loads(request.data))
        captured["timeout"] = kwargs["timeout"]
        return FakeResponse(value)

    audited = StructuredLLMClient(
        "https://api.openai.com/v1", "secret", "gpt-6.1-sol",
        max_output_tokens=16_384, timeout_seconds=180,
        provider="openai", reasoning_effort="low", service_tier="default",
    )
    with patch("personal_brain.llm_client.urlopen", side_effect=fake_urlopen):
        audited.generate_with_metadata("system", {}, {"type": "object"})

    assert captured["max_completion_tokens"] == 16_384
    assert captured["timeout"] == 180


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_output_tokens": 32_769}, "max_output_tokens"),
        ({"timeout_seconds": 301}, "timeout_seconds"),
    ],
)
def test_transport_rejects_values_beyond_audited_bounds(overrides, message):
    values = {
        "max_output_tokens": 16_384,
        "timeout_seconds": 180,
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        StructuredLLMClient(
            "https://api.openai.com/v1", "secret", "gpt-6.1-sol",
            provider="openai", reasoning_effort="low", service_tier="default",
            **values,
        )


def test_generate_with_metadata_and_legacy_generate_are_compatible():
    with patch("personal_brain.llm_client.urlopen", return_value=FakeResponse(response())):
        result = client().generate_with_metadata("system", {"input": True}, {"type": "object"})
    assert result.output == {"answer": "ok"}
    assert result.provider == "fixture-provider"
    assert result.model == "provider-model-version"
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7
    assert result.request_id == "req-header"

    with patch("personal_brain.llm_client.urlopen", return_value=FakeResponse(response())):
        assert client().generate("system", {}, {"type": "object"}) == {"answer": "ok"}


def test_invalid_known_response_preserves_usage_for_accounting():
    value = response()
    value["choices"][0]["message"]["content"] = "not-json"
    with patch("personal_brain.llm_client.urlopen", return_value=FakeResponse(value)):
        with pytest.raises(StructuredLLMError) as caught:
            client().generate_with_metadata("system", {}, {"type": "object"})
    assert caught.value.outcome_unknown is False
    assert caught.value.usage.total_tokens == 18
    assert caught.value.request_id == "req-header"
    assert caught.value.error_kind == "invalid_json_response"


def test_reasoning_exhaustion_is_classified_before_json_parsing():
    value = response()
    value["choices"][0] = {
        "finish_reason": "length",
        "message": {"content": None, "refusal": None},
    }
    value["usage"] = {
        "prompt_tokens": 1054,
        "completion_tokens": 950,
        "total_tokens": 2004,
        "completion_tokens_details": {"reasoning_tokens": 950},
    }
    with patch("personal_brain.llm_client.urlopen", return_value=FakeResponse(value)):
        with pytest.raises(StructuredLLMError) as caught:
            configured_reasoning_client().generate_with_metadata(
                "system", {}, {"type": "object"}
            )
    assert caught.value.error_kind == "output_budget_exhausted"
    assert caught.value.outcome_unknown is False
    assert caught.value.finish_reason == "length"
    assert caught.value.usage.reasoning_tokens == 950
    assert caught.value.request_id == "req-header"


def test_refusal_is_distinct_and_preserves_known_usage():
    value = response()
    value["choices"][0] = {
        "finish_reason": "stop",
        "message": {"content": None, "refusal": "Request refused"},
    }
    with patch("personal_brain.llm_client.urlopen", return_value=FakeResponse(value)):
        with pytest.raises(StructuredLLMError) as caught:
            client().generate_with_metadata("system", {}, {"type": "object"})
    assert caught.value.error_kind == "provider_refusal"
    assert caught.value.refusal == "Request refused"
    assert caught.value.usage.total_tokens == 18


def test_content_filter_is_distinct_from_empty_content():
    value = response()
    value["choices"][0] = {
        "finish_reason": "content_filter",
        "message": {"content": None, "refusal": None},
    }
    with patch("personal_brain.llm_client.urlopen", return_value=FakeResponse(value)):
        with pytest.raises(StructuredLLMError) as caught:
            client().generate_with_metadata("system", {}, {"type": "object"})
    assert caught.value.error_kind == "content_filter"
    assert caught.value.finish_reason == "content_filter"


def test_empty_completed_response_is_not_invalid_json():
    value = response()
    value["choices"][0]["message"]["content"] = ""
    with patch("personal_brain.llm_client.urlopen", return_value=FakeResponse(value)):
        with pytest.raises(StructuredLLMError) as caught:
            client().generate_with_metadata("system", {}, {"type": "object"})
    assert caught.value.error_kind == "empty_provider_response"


def test_schema_rejection_is_distinct_and_preserves_provider_request_id():
    error = HTTPError(
        "https://api.openai.com/v1/chat/completions",
        400,
        "Bad Request",
        {"x-request-id": "req-rejected"},
        BytesIO(b'{"error":{"message":"invalid_json_schema"}}'),
    )
    with patch("personal_brain.llm_client.urlopen", side_effect=error):
        with pytest.raises(StructuredLLMError) as caught:
            client().generate_with_metadata("system", {}, {"type": "object"})
    assert caught.value.error_kind == "provider_rejection"
    assert caught.value.outcome_unknown is False
    assert caught.value.request_id == "req-rejected"


def test_transport_failure_is_conservatively_unknown():
    with patch("personal_brain.llm_client.urlopen", side_effect=URLError("offline")):
        with pytest.raises(StructuredLLMError) as caught:
            client().generate_with_metadata("system", {}, {"type": "object"})
    assert caught.value.outcome_unknown is True
    assert caught.value.usage.total_tokens == 0
    assert caught.value.error_kind == "transport_error"


def test_timeout_is_distinct_and_conservatively_unknown():
    with patch("personal_brain.llm_client.urlopen", side_effect=TimeoutError()):
        with pytest.raises(StructuredLLMError) as caught:
            client().generate_with_metadata("system", {}, {"type": "object"})
    assert caught.value.error_kind == "timeout"
    assert caught.value.outcome_unknown is True


def test_reasoning_parameters_and_detailed_usage_are_preserved():
    value = response()
    value["service_tier"] = "default"
    value["usage"]["prompt_tokens_details"] = {
        "cached_tokens": 3, "cache_write_tokens": 2,
    }
    value["usage"]["completion_tokens_details"] = {"reasoning_tokens": 5}
    captured = {}

    def fake_urlopen(request, **_kwargs):
        captured.update(json.loads(request.data))
        return FakeResponse(value)

    with patch("personal_brain.llm_client.urlopen", side_effect=fake_urlopen):
        result = configured_reasoning_client().generate_with_metadata(
            "system", {}, {"type": "object"}
        )
    assert captured["reasoning_effort"] == "low"
    assert captured["service_tier"] == "default"
    assert captured["max_completion_tokens"] == 1024
    assert captured["prompt_cache_options"] == {"mode": "explicit"}
    assert result.service_tier == "default"
    assert result.usage.cached_input_tokens == 3
    assert result.usage.cache_write_input_tokens == 2
    assert result.usage.reasoning_tokens == 5
