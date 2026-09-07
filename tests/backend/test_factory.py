from annotation.providers import MockProvider, OpenAICompatibleProvider, create_provider_from_env


def test_model_provider_env_switch(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    assert isinstance(create_provider_from_env(), MockProvider)

    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.setenv("MODEL_NAME", "deepseek-chat")
    provider = create_provider_from_env()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.provider == "deepseek"
    assert provider.base_url == "https://api.deepseek.com/v1"
    assert provider.model == "deepseek-chat"
