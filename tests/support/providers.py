"""Test-only deterministic provider doubles."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from annotation.offline.provider import OfflineWorkflowCapabilities
from annotation.providers.models import (
    GenerationRequest,
    GenerationResponse,
    ModelEvent,
    ProviderCapabilities,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)


class SequenceProvider:
    """Replay Blueprint responses with explicit downstream workflow fixtures."""

    provider = "sequence"
    base_url = None

    class _WorkflowFixtures(OfflineWorkflowCapabilities):
        def use_blueprint_fixture(self, provider: Any) -> bool:
            return False

        def blueprint_source_refs(self, unit: Any, state: Mapping[str, Any]) -> tuple[list[str], bool]:
            return list(unit.source_refs), False

        def blueprint_artifact_id(self, fallback: str) -> str:
            return fallback

    def __init__(
        self,
        responses: Sequence[Mapping[str, Any] | ProviderError | Exception | Callable[[StructuredGenerationRequest[Any]], Any]],
        *,
        model: str = "sequence-fixture",
        config_version: str = "test-sequence-v1",
    ) -> None:
        self.model = model
        self.config_version = config_version
        self.capabilities = ProviderCapabilities(supports_structured_output=True, supports_json_schema=True)
        self.workflow = self._WorkflowFixtures()
        self._responses = list(responses)
        self.calls: list[StructuredGenerationRequest[Any]] = []

    def check_capability(self, capability: str) -> bool:
        return bool(getattr(self.capabilities, capability, False))

    capability_check = check_capability

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(text="", provider=self.provider, model=self.model, base_url=None,
                                  config_version=self.config_version, duration_ms=0)

    def stream(self, request: GenerationRequest):
        yield ModelEvent(type="done")

    def generate_structured(
        self, request: StructuredGenerationRequest[BaseModel]
    ) -> StructuredGenerationResponse[BaseModel]:
        self.calls.append(request)
        if not self._responses:
            raise ProviderError("sequence provider exhausted", category="sequence")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            if isinstance(item, ProviderError):
                raise item
            raise ProviderError(str(item), category="sequence") from item
        if callable(item):
            item = item(request)
        if isinstance(item, BaseException):
            if isinstance(item, ProviderError):
                raise item
            raise ProviderError(str(item), category="sequence") from item
        if isinstance(item, BaseModel):
            value = request.schema.model_validate(item.model_dump(mode="python"))
            raw_text = value.model_dump_json()
        else:
            raw_text = json.dumps(item, ensure_ascii=False, default=str)
            try:
                value = request.schema.model_validate(item)
            except Exception as exc:
                raise ProviderError(
                    f"sequence structured payload does not match schema: {exc}",
                    category="schema",
                    raw_output=raw_text,
                ) from exc
        return StructuredGenerationResponse(
            value=value,
            raw_text=raw_text,
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            config_version=self.config_version,
            duration_ms=0,
        )
