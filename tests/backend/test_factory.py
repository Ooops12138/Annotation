from annotation.offline import OfflineFixtureProvider
from annotation.providers import OpenAICompatibleProvider, create_provider_from_env


def test_model_provider_env_switch(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROFILE", "offline")
    monkeypatch.setenv("MODEL_PROVIDER", "")
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    assert isinstance(create_provider_from_env(), OfflineFixtureProvider)

    monkeypatch.setenv("MODEL_PROFILE", "live")
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.setenv("MODEL_NAME", "deepseek-chat")
    provider = create_provider_from_env()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.provider == "deepseek"
    assert provider.base_url == "https://api.deepseek.com/v1"
    assert provider.model == "deepseek-chat"
    assert provider.thinking == "disabled"


def test_live_profile_requires_an_explicit_provider(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROFILE", "live")
    monkeypatch.setenv("MODEL_PROVIDER", "")
    try:
        create_provider_from_env()
    except ValueError as exc:
        assert "MODEL_PROVIDER is required" in str(exc)
    else:
        raise AssertionError("live profile must not select offline fixtures")


def test_live_profile_rejects_removed_mock_provider(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROFILE", "live")
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    try:
        create_provider_from_env()
    except ValueError as exc:
        assert "mock providers were removed" in str(exc)
    else:
        raise AssertionError("mock provider must not remain in live profile")


def test_offline_profile_ignores_a_live_provider_setting(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROFILE", "offline")
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    assert isinstance(create_provider_from_env(), OfflineFixtureProvider)
