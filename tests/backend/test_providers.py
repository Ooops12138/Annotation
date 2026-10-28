import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from pydantic import BaseModel

from annotation.workflow.models import BlueprintDraft, ContentDraft

from annotation.offline import OfflineFixtureProvider
from annotation.providers import (
    GenerationRequest,
    ProviderCapabilities,
    StructuredGenerationRequest,
    OpenAICompatibleProvider,
    OpenAIProvider,
    ProviderError,
)


class EmptyPayload(BaseModel):
    pass


def test_offline_provider_contract_and_metadata() -> None:
    provider = OfflineFixtureProvider()
    response = provider.generate(GenerationRequest(prompt="hello"))
    assert response.provider == "offline"
    assert response.model == "offline-fixture"
    assert provider.check_capability("supports_streaming")
    assert [event.type for event in provider.stream(GenerationRequest(prompt="hello"))] == [
        "token",
        "done",
    ]


def test_offline_structured_output_validates_against_requested_schema() -> None:
    response = OfflineFixtureProvider().generate_structured(
        StructuredGenerationRequest(prompt="{}", schema=EmptyPayload)
    )
    assert isinstance(response.value, EmptyPayload)


def test_offline_builtin_fixtures_are_selected_per_schema() -> None:
    provider = OfflineFixtureProvider()
    blueprint = provider.generate_structured(
        StructuredGenerationRequest(prompt="{}", schema=BlueprintDraft)
    )
    document = provider.generate_structured(
        StructuredGenerationRequest(prompt="{}", schema=ContentDraft)
    )
    assert isinstance(blueprint.value, BlueprintDraft)
    assert isinstance(document.value, ContentDraft)


def test_capabilities_have_explicit_false_defaults() -> None:
    capabilities = ProviderCapabilities()
    assert not capabilities.supports_tools
    assert not capabilities.supports_vision


class _FakeMessage:
    content = '{"value": "ok"}'


class _FakeChoice:
    message = _FakeMessage()


class _FakeCompletions:
    def create(self, **kwargs):
        return type("Response", (), {"choices": [_FakeChoice()], "usage": None})()


class _FakeClient:
    chat = type("Chat", (), {"completions": _FakeCompletions()})()


class TinyPayload(BaseModel):
    value: str


class _RecordingCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"choices": [_FakeChoice()], "usage": None})()


def test_openai_and_compatible_endpoint_contracts_support_structured_output() -> None:
    request = StructuredGenerationRequest(prompt="{}", schema=TinyPayload)
    openai = OpenAIProvider(model="test-openai", client=_FakeClient())
    assert openai.generate_structured(request).value.value == "ok"
    for name in ("ollama", "vllm", "deepseek"):
        compatible = OpenAICompatibleProvider(
            model="test-compatible",
            base_url=f"http://{name}/v1",
            provider_name=name,
            client=_FakeClient(),
        )
        assert compatible.provider == name
        assert compatible.generate_structured(request).value.value == "ok"


def test_thinking_disabled_is_sent_as_compatible_extra_body(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MODEL_CALL_LOG_PATH", str(tmp_path / "model-calls.jsonl"))
    completions = _RecordingCompletions()
    client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
    provider = OpenAICompatibleProvider(
        model="test-deepseek",
        base_url="https://api.deepseek.com/v1",
        provider_name="deepseek",
        thinking="disabled",
        client=client,
    )
    provider.generate(GenerationRequest(prompt="hello"))
    assert completions.calls[-1]["extra_body"] == {"thinking": {"type": "disabled"}}
    record = json.loads((tmp_path / "model-calls.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["input"]["prompt"] == "hello"
    assert record["input"]["thinking"] == "disabled"
    assert record["output"] == '{"value": "ok"}'


@pytest.mark.parametrize("cls,category,retryable", [
    (openai.RateLimitError, "rate_limit", True),
    (openai.InternalServerError, "transport", True),
    (openai.AuthenticationError, "authentication", False),
    (openai.PermissionDeniedError, "authentication", False),
    (openai.APITimeoutError, "timeout", True),
    (openai.APIConnectionError, "transport", True),
])
def test_sdk_errors_are_classified_by_type(cls, category, retryable):
    from annotation.providers.openai import _normalize_error
    request = httpx.Request("POST", "https://example.test/v1")
    if issubclass(cls, openai.APIStatusError):
        exc = cls("error", response=httpx.Response(500, request=request), body=None)
    else:
        exc = cls(request=request)
    result = _normalize_error(exc)
    assert (result.category, result.retryable) == (category, retryable)


def test_unrelated_names_and_message_numbers_do_not_imply_rate_limit():
    from annotation.providers.openai import _normalize_error
    class GenerateError(Exception):
        pass
    for exc in (GenerateError("failed"), ValueError("record 429 is invalid")):
        result = _normalize_error(exc)
        assert result.category == "provider"
        assert not result.retryable


@pytest.mark.parametrize("raw", ['{"value":', '{"value":"ok"}'])
def test_finish_reason_length_is_truncation_even_for_valid_json(raw, tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CALL_LOG_PATH", str(tmp_path / "calls.jsonl"))
    response = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=raw), finish_reason="length")], usage=None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
    provider = OpenAIProvider(model="test", client=client)
    with pytest.raises(ProviderError) as info:
        provider.generate_structured(StructuredGenerationRequest(prompt="test", schema=TinyPayload))
    assert info.value.category == "truncation"
    assert not info.value.retryable
    assert info.value.raw_output == raw
    record = json.loads((tmp_path / "calls.jsonl").read_text(encoding="utf-8"))
    assert record["finish_reason"] == "length"


@pytest.mark.parametrize("failure", [False, True])
def test_log_failure_preserves_response_or_original_error(tmp_path, monkeypatch, caplog, failure):
    # A regular file cannot be used as the parent of the log file.
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied")
    monkeypatch.setenv("MODEL_CALL_LOG_PATH", str(parent / "calls.jsonl"))
    if failure:
        request = httpx.Request("POST", "https://example.test/v1")
        def create(**kwargs):
            raise openai.APITimeoutError(request=request)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    else:
        client = _FakeClient()
    provider = OpenAIProvider(model="test", client=client)
    request = StructuredGenerationRequest(prompt="test", schema=TinyPayload)
    if failure:
        with pytest.raises(ProviderError) as info:
            provider.generate_structured(request)
        assert info.value.category == "timeout"
        assert isinstance(info.value.__cause__, openai.APITimeoutError)
    else:
        assert provider.generate_structured(request).value.value == "ok"
    assert "Failed to write model call log" in caplog.text
