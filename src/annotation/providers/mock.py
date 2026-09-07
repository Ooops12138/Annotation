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
                    "title": "实数系与复数系（离线回归）",
                    "knowledge_units": [
                        {"title": "域公理、序公理与区间", "kind": "concept", "learning_objectives": ["说明实数的代数和有序结构", "区分开区间、闭区间和半开区间"], "teaching_materials": ["公理", "区间定义"], "source_refs": []},
                        {"title": "整数、有理数与无理数", "kind": "theorem", "learning_objectives": ["解释唯一因数分解的内容", "区分有理数与无理数"], "prerequisites": ["域公理、序公理与区间"], "teaching_materials": ["唯一因数分解定理", "无理数证明"], "source_refs": []},
                        {"title": "上界、上确界与完全公理", "kind": "formula", "learning_objectives": ["区分最大元与上确界", "理解完全公理的作用"], "prerequisites": ["域公理、序公理与区间"], "teaching_materials": ["上确界定义", "完全公理"], "source_refs": []},
                        {"title": "复数、复平面与绝对值", "kind": "example", "learning_objectives": ["把复数写成 x+iy", "用复平面解释复数绝对值"], "prerequisites": ["域公理、序公理与区间"], "teaching_materials": ["复平面", "模的计算"], "source_refs": []},
                    ],
                }
            elif request.schema.__name__ == "DocumentDraft":
                payload = {
                    "title": "实数系与复数系 · 离线回归样例",
                    "section_title": "实数结构、完全性与复数概览",
                    "explanation": "本章以实数的域结构、序结构和完全性为主线，依次建立区间、整数、有理数、无理数、上界与上确界等概念，再把数系扩充到复数。离线样例用于验证：知识结构不是简单目录，公式、例题、测验和来源引用可以在同一条 artifact 链路中传递。",
                    "formula_latex": r"\alpha=\sup S,\qquad z=x+iy,\qquad |z|=\sqrt{x^2+y^2}",
                    "quiz_question": "集合 S=[0,1) 的最大元与上确界分别是什么？",
                    "quiz_options": ["最大元为 1，上确界为 1", "没有最大元，上确界为 1", "没有最大元，也没有上确界"],
                    "quiz_answer": "没有最大元，上确界为 1",
                    "quiz_explanation": "1 不属于 S，因此不是最大元；但 1 是 S 的最小上界。",
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
