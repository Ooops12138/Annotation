"""Deterministic provider for local development and workflow tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
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
    # Only this built-in fixture is allowed to adapt empty source refs for
    # deterministic offline regression tests.  Real or sequence providers
    # must preserve the model output exactly.
    fixture_adaptation = True

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
                        {"knowledge_unit_id": "ku-real-structure", "title": "实数公理与基本性质", "kind": "concept", "learning_objectives": ["说明实数的代数和有序结构", "区分开区间、闭区间和半开区间"], "source_refs": [], "related_unit_ids": []},
                        {"knowledge_unit_id": "ku-supremum", "title": "上确界与完全公理", "kind": "formula", "learning_objectives": ["区分最大元与上确界", "用逼近性质理解上确界", "理解完全公理的作用"], "prerequisites": ["ku-real-structure"], "source_refs": [], "related_unit_ids": ["ku-irrational"]},
                        {"knowledge_unit_id": "ku-integers-rationals", "title": "整数与有理数", "kind": "theorem", "learning_objectives": ["说明整数的基本性质", "解释有理数的表示和稠密性"], "prerequisites": ["ku-real-structure"], "source_refs": [], "related_unit_ids": ["ku-irrational"]},
                        {"knowledge_unit_id": "ku-irrational", "title": "无理数", "kind": "theorem", "learning_objectives": ["区分有理数与无理数", "说明非完全平方数平方根的无理性论证", "根据教材复述 e 的无理性证明"], "prerequisites": ["ku-integers-rationals", "ku-supremum"], "source_refs": [], "related_unit_ids": ["ku-supremum"]},
                        {"knowledge_unit_id": "ku-absolute-inequality", "title": "绝对值与不等式", "kind": "formula", "learning_objectives": ["解释绝对值的几何意义", "推导三角不等式或柯西-施瓦茨不等式"], "prerequisites": ["ku-real-structure"], "source_refs": [], "related_unit_ids": ["ku-complex"]},
                        {"knowledge_unit_id": "ku-complex", "title": "复数系及其运算", "kind": "example", "learning_objectives": ["把复数写成 x+iy", "计算复数的模和辐角", "用指数形式表达复数并识别主值"], "prerequisites": ["ku-real-structure", "ku-absolute-inequality"], "source_refs": [], "related_unit_ids": []},
                    ],
                }
            elif request.schema.__name__ == "DocumentDraft":
                payload = {
                    "title": "实数系与复数系 · 离线回归样例",
                    "section_title": "实数结构、完全性与复数概览",
                    "explanation": "本章以实数的域结构、序结构和完全性为主线，依次建立区间、整数、有理数、无理数、上界与上确界等概念，再把数系扩充到复数。离线样例用于验证：知识结构不是简单目录，公式、例题、测验和来源引用可以在同一条 artifact 链路中传递。",
                    "quiz_question": "集合 S=[0,1) 的最大元与上确界分别是什么？",
                    "quiz_options": ["最大元为 1，上确界为 1", "没有最大元，上确界为 1", "没有最大元，也没有上确界"],
                    "quiz_answer": "没有最大元，上确界为 1",
                    "quiz_explanation": "1 不属于 S，因此不是最大元；但 1 是 S 的最小上界。",
                    "source_refs": [],
                }
            elif request.schema.__name__ == "ContentDraft":
                payload = {
                    "title": "知识单元讲解（离线回归）",
                    "content": "## 讲解\n\n这是按单个知识单元生成的离线回归讲解。实际教材事实由 ContextPack 中的来源片段支持。",
                    "callouts": [],
                    "source_refs": [],
                }
        try:
            value = request.schema.model_validate(payload)
        except Exception as exc:
            raise ProviderError(
                f"mock structured payload does not match schema: {exc}",
                category="schema",
                raw_output=json.dumps(payload, ensure_ascii=False, default=str),
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


class SequenceProvider(MockProvider):
    """Deterministic provider for replaying a finite structured-output sequence.

    This is intentionally small and local: it gives loop tests a provider that
    can emit a blocking candidate, a repaired candidate, or a typed
    ``ProviderError`` without making a network call.
    """

    # It deliberately follows the offline MockProvider content path after the
    # Blueprint sequence is consumed, so loop tests count only Blueprint calls.
    provider = "mock"
    fixture_adaptation = False

    def __init__(
        self,
        responses: Sequence[Mapping[str, Any] | ProviderError | Exception | Callable[[StructuredGenerationRequest[Any]], Any]],
        *,
        model: str = "sequence-fixture",
        config_version: str = "mock-sequence-v1",
    ) -> None:
        super().__init__(model=model, config_version=config_version)
        # A non-empty marker prevents the Blueprint graph from treating this
        # explicit sequence as the built-in empty-source fixture.
        self.structured_payload = {"__sequence_provider__": True}
        self._responses = list(responses)
        self.calls: list[StructuredGenerationRequest[Any]] = []

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
