from pydantic import BaseModel

from annotation.workflow.graph import BlueprintDraft, DocumentDraft

from annotation.providers import (
    GenerationRequest,
    MockProvider,
    ProviderCapabilities,
    StructuredGenerationRequest,
    OpenAICompatibleProvider,
    OpenAIProvider,
)


class EmptyPayload(BaseModel):
    pass


def test_mock_provider_contract_and_metadata() -> None:
    provider = MockProvider()
    response = provider.generate(GenerationRequest(prompt="hello"))
    assert response.provider == "mock"
    assert response.model == "fixture-model"
    assert provider.check_capability("supports_streaming")
    assert [event.type for event in provider.stream(GenerationRequest(prompt="hello"))] == [
        "token",
        "done",
    ]


def test_mock_structured_output_validates_against_requested_schema() -> None:
    response = MockProvider().generate_structured(
        StructuredGenerationRequest(prompt="{}", schema=EmptyPayload)
    )
    assert isinstance(response.value, EmptyPayload)


def test_mock_builtin_fixtures_are_selected_per_schema() -> None:
    provider = MockProvider()
    blueprint = provider.generate_structured(
        StructuredGenerationRequest(prompt="{}", schema=BlueprintDraft)
    )
    document = provider.generate_structured(
        StructuredGenerationRequest(prompt="{}", schema=DocumentDraft)
    )
    assert isinstance(blueprint.value, BlueprintDraft)
    assert isinstance(document.value, DocumentDraft)


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
