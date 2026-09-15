"""Provider-neutral request, response and capability contracts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_structured_output: bool = False
    supports_tools: bool = False
    supports_streaming: bool = False
    supports_vision: bool = False
    supports_json_schema: bool = False
    context_window: int | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class GenerationRequest:
    prompt: str
    system_prompt: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class StructuredGenerationRequest:
    prompt: str
    schema: type[T]
    system_prompt: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerationResponse:
    text: str
    provider: str
    model: str
    base_url: str | None
    config_version: str
    duration_ms: int
    usage: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class StructuredGenerationResponse:
    value: T
    raw_text: str
    provider: str
    model: str
    base_url: str | None
    config_version: str
    duration_ms: int
    usage: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelEvent:
    type: str
    text: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """A normalized provider failure suitable for workflow error handling."""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        retryable: bool = False,
        raw_output: str = "",
        parsed_output: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.raw_output = raw_output
        self.parsed_output = parsed_output


class ModelProvider(Protocol):
    provider: str
    model: str
    base_url: str | None
    config_version: str
    capabilities: ProviderCapabilities

    def generate(self, request: GenerationRequest) -> GenerationResponse: ...

    def generate_structured(
        self, request: StructuredGenerationRequest[T]
    ) -> StructuredGenerationResponse[T]: ...

    def stream(self, request: GenerationRequest) -> Iterator[ModelEvent]: ...

    def check_capability(self, capability: str) -> bool: ...

    def capability_check(self, capability: str) -> bool: ...
