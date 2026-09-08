"""Environment-based provider construction for API/CLI entry points."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .mock import MockProvider
from .models import ModelProvider
from .openai import OpenAICompatibleProvider, OpenAIProvider


def create_provider_from_env() -> ModelProvider:
    # `.env` is local runtime configuration; values already present in the
    # process take precedence (useful for tests and deployment environments).
    load_dotenv(Path(".env"), override=False)
    kind = os.getenv("MODEL_PROVIDER", "mock").strip().lower()
    model = os.getenv("MODEL_NAME") or ("deepseek-chat" if kind == "deepseek" else "fixture-model")
    config_version = os.getenv("MODEL_CONFIG_VERSION", "env-v1")
    # Reasoning-capable models may spend longer than the lightweight mock
    # path before returning their final JSON payload.
    timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "120"))
    max_retries = int(os.getenv("MODEL_MAX_RETRIES", "1"))
    thinking = None
    if kind == "deepseek":
        thinking = (os.getenv("MODEL_THINKING") or "disabled").strip().lower()
    api_key = os.getenv("MODEL_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("MODEL_BASE_URL")
    if kind == "mock":
        return MockProvider(model=model, config_version=config_version)
    if kind == "openai":
        return OpenAIProvider(
            model=model,
            api_key=api_key,
            base_url=base_url or "https://api.openai.com/v1",
            config_version=config_version,
            timeout=timeout,
            max_retries=max_retries,
            thinking=thinking,
        )
    if kind in {"openai-compatible", "ollama", "vllm", "deepseek"}:
        if kind == "deepseek" and not base_url:
            base_url = "https://api.deepseek.com/v1"
        if not base_url:
            raise ValueError("MODEL_BASE_URL is required for an OpenAI-compatible provider")
        return OpenAICompatibleProvider(
            model=model,
            api_key=api_key,
            base_url=base_url,
            provider_name=kind,
            config_version=config_version,
            timeout=timeout,
            max_retries=max_retries,
            thinking=thinking,
        )
    raise ValueError(f"Unsupported MODEL_PROVIDER: {kind}")
