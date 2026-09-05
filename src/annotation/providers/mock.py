"""Deterministic provider for local development and workflow tests."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import BaseModel

from .models import (
    GenerationRequest,
    GenerationResponse,
    ModelEvent,
    ProviderCapabilities,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    ProviderError,
)


class MockProvider:
    provider = "mock"

    def __init__(
        self,
        *,
        model: str = "fixture-model",
        config_version: str = "mock-v1",
        structured_payload: Mapping[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.base_url = None
        self.config_version = config_version
        # A caller-provided payload is used for every request (handy for
        # contract tests).  Built-in fixtures, however, must be selected per
        # requested schema; otherwise the Blueprint fixture is accidentally
        # reused for DocumentDraft and the workflow falls back.
        self.structured_payload = dict(structured_payload or {})
        self.capabilities = ProviderCapabilities(
            supports_structured_output=True,
            supports_streaming=True,
            supports_json_schema=True,
        )

    def check_capability(self, capability: str) -> bool:
        return bool(getattr(self.capabilities, capability, False))

    capability_check = check_capability

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(
            text=f"Mock response: {request.prompt}",
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            config_version=self.config_version,
            duration_ms=0,
        )

    def generate_structured(
        self, request: StructuredGenerationRequest[BaseModel]
    ) -> StructuredGenerationResponse[BaseModel]:
        payload = self.structured_payload
        if not payload:
            if request.schema.__name__ == "BlueprintDraft":
                payload = {
                    "title": "教材章节（Mock 生成）",
                    "knowledge_units": [
                        {"title": "章节核心概念", "kind": "concept", "learning_objectives": ["理解核心概念"], "source_refs": []},
                        {"title": "定义与基本性质", "kind": "formula", "learning_objectives": ["掌握定义和性质"], "source_refs": []},
                        {"title": "典型例题与方法", "kind": "example", "learning_objectives": ["能够应用方法解题"], "source_refs": []},
                    ],
                }
            elif request.schema.__name__ == "DocumentDraft":
                payload = {
                    "title": "教材章节学习文档",
                    "section_title": "核心内容",
                    "explanation": "这是基于教材 SourceBlock 生成的 Mock 讲解，用于离线验证完整数据链路。",
                    "formula_latex": r"\\lim_{x \\to a} f(x)=L",
                    "quiz_question": "本 Demo 的内容主来源是什么？",
                    "quiz_options": ["教材 PDF", "随机外部资料", "未提供来源"],
                    "quiz_answer": "教材 PDF",
                    "quiz_explanation": "所有内容应通过 source_refs 回溯到教材片段。",
                    "source_refs": [],
                }
        try:
            value = request.schema.model_validate(payload)
        except Exception as exc:
            raise ProviderError(
                f"mock structured payload does not match schema: {exc}",
                category="schema",
            ) from exc
        return StructuredGenerationResponse(
            value=value,
            raw_text=value.model_dump_json(),
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            config_version=self.config_version,
            duration_ms=0,
        )

    def stream(self, request: GenerationRequest) -> Iterator[ModelEvent]:
        yield ModelEvent(type="token", text=f"Mock response: {request.prompt}")
        yield ModelEvent(type="done")
