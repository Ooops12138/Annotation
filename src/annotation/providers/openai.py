"""OpenAI and OpenAI-compatible ModelProvider implementations."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import replace
from typing import Any, Generic, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from .models import (
    GenerationRequest,
    GenerationResponse,
    ModelEvent,
    ProviderCapabilities,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)

T = TypeVar("T", bound=BaseModel)


class _OpenAIBase(Generic[T]):
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        config_version: str = "v1",
        timeout: float = 60.0,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.config_version = config_version
        self.capabilities = ProviderCapabilities(
            supports_structured_output=True,
            supports_streaming=True,
            # The adapter requests portable JSON objects; strict provider-specific
            # JSON-schema response formats are intentionally not assumed here.
            supports_json_schema=False,
        )
        self._client = client or OpenAI(
            api_key=api_key or "dummy-key",
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    def check_capability(self, capability: str) -> bool:
        return bool(getattr(self.capabilities, capability, False))

    capability_check = check_capability

    def _messages(self, request: GenerationRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        return messages

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=self._messages(request),
                temperature=request.temperature,
                max_tokens=request.max_output_tokens,
            )
            text = response.choices[0].message.content or ""
            usage = _usage(response)
            return GenerationResponse(
                text=text,
                provider=self.provider,
                model=self.model,
                base_url=self.base_url,
                config_version=self.config_version,
                duration_ms=_duration_ms(started),
                usage=usage,
            )
        except Exception as exc:  # SDK exceptions vary between compatible servers.
            raise _normalize_error(exc) from exc

    def generate_structured(
        self, request: StructuredGenerationRequest[T]
    ) -> StructuredGenerationResponse[T]:
        if not self.check_capability("supports_structured_output"):
            raise ProviderError(
                "structured output is not supported by this provider",
                category="capability",
            )
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=self._messages(
                    GenerationRequest(
                        prompt=request.prompt,
                        system_prompt=request.system_prompt,
                        temperature=request.temperature,
                        max_output_tokens=request.max_output_tokens,
                    )
                ),
                temperature=request.temperature,
                max_tokens=request.max_output_tokens,
                response_format={"type": "json_object"},
            )
            raw_text = response.choices[0].message.content or "{}"
            value = request.schema.model_validate(json.loads(raw_text))
            return StructuredGenerationResponse(
                value=value,
                raw_text=raw_text,
                provider=self.provider,
                model=self.model,
                base_url=self.base_url,
                config_version=self.config_version,
                duration_ms=_duration_ms(started),
                usage=_usage(response),
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProviderError(
                f"structured response validation failed: {exc}",
                category="schema",
            ) from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise _normalize_error(exc) from exc

    def stream(self, request: GenerationRequest) -> Iterator[ModelEvent]:
        if not self.check_capability("supports_streaming"):
            raise ProviderError("streaming is not supported", category="capability")
        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=self._messages(request),
                temperature=request.temperature,
                max_tokens=request.max_output_tokens,
                stream=True,
            )
            for chunk in stream:
                text = chunk.choices[0].delta.content or ""
                if text:
                    yield ModelEvent(type="token", text=text)
            yield ModelEvent(type="done")
        except Exception as exc:
            raise _normalize_error(exc) from exc


class OpenAIProvider(_OpenAIBase[Any]):
    provider = "openai"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", "https://api.openai.com/v1")
        super().__init__(**kwargs)
        self.capabilities = replace(self.capabilities, supports_json_schema=True)


class OpenAICompatibleProvider(_OpenAIBase[Any]):
    provider = "openai-compatible"

    def __init__(self, *, base_url: str, provider_name: str | None = None, **kwargs: Any) -> None:
        super().__init__(base_url=base_url, **kwargs)
        if provider_name:
            self.provider = provider_name


def _duration_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        key: int(value)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if (value := getattr(usage, key, None)) is not None
    }


def _normalize_error(exc: Exception) -> ProviderError:
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return ProviderError(str(exc), category="timeout", retryable=True)
    if "rate" in name or "429" in str(exc):
        return ProviderError(str(exc), category="rate_limit", retryable=True)
    if "authentication" in name or "permission" in name:
        return ProviderError(str(exc), category="authentication")
    if "connection" in name or "apierror" in name:
        return ProviderError(str(exc), category="transport", retryable=True)
    return ProviderError(str(exc), category="provider")
