import json
from urllib.error import URLError
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
        "choices": [{"message": {"content": json.dumps({"answer": "ok"})}}],
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


def test_transport_failure_is_conservatively_unknown():
    with patch("personal_brain.llm_client.urlopen", side_effect=URLError("offline")):
        with pytest.raises(StructuredLLMError) as caught:
            client().generate_with_metadata("system", {}, {"type": "object"})
    assert caught.value.outcome_unknown is True
    assert caught.value.usage.total_tokens == 0


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
