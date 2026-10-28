"""Deterministic fixtures for offline development and regression runs."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import BaseModel

from annotation.providers.models import (
    GenerationRequest,
    GenerationResponse,
    ModelEvent,
    ProviderCapabilities,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    WorkflowProviderCapabilities,
)
from .content import content_critique, content_draft
from .course import task_plan
from .components import component_draft
from .quiz import quiz_draft


class OfflineWorkflowCapabilities(WorkflowProviderCapabilities):
    """Workflow fixtures supported by the explicit offline profile."""

    def use_blueprint_fixture(self, provider: Any) -> bool:
        return not bool(getattr(provider, "structured_payload", {}))

    def blueprint_draft(self, schema: type[BaseModel], payload: Mapping[str, Any]) -> BaseModel:
        return schema.model_validate(payload)

    def blueprint_source_refs(self, unit: Any, state: Mapping[str, Any]) -> tuple[list[str], bool]:
        valid = set(state["source_refs"])
        refs = [ref for ref in unit.source_refs if ref in valid]
        if refs:
            return refs, False
        return list(state["source_refs"][:3]), True

    def blueprint_artifact_id(self, fallback: str) -> str:
        return "bp-fixture-001"

    def fixture_metadata(self, agent: str) -> dict[str, Any]:
        return {"agent": agent, "provider": "offline", "model": "offline-fixture",
                "base_url": None, "config_version": "offline-v1", "duration_ms": 0,
                "usage": {}, "fixture_adaptation": True}

    def plan_content_tasks(self, blueprint: Any) -> BaseModel:
        return task_plan(blueprint)

    def content_draft(self, unit: Any, pack: Any) -> BaseModel:
        return content_draft(unit, pack)

    def content_critique(self) -> BaseModel:
        return content_critique()

    def quiz_draft(self, unit: Any, pack: Any, requested_count: int | None) -> BaseModel:
        return quiz_draft(unit, pack, requested_count)

    def component_draft(self, task: Any) -> BaseModel:
        return component_draft(task)

    def component_critique(self) -> BaseModel:
        from annotation.domain.artifacts import InteractiveComponentCritiqueDraft
        return InteractiveComponentCritiqueDraft()

    def content_retrieval_decision(self, pack: Any, unit: Any) -> BaseModel:
        from annotation.workflow.models import RetrievalDecision
        return RetrievalDecision(action="write") if pack.excerpts else RetrievalDecision(
            action="search", textbook_queries=[unit.title])

    def quiz_retrieval_decision(self) -> BaseModel:
        from annotation.workflow.models import QuizRetrievalDecision
        return QuizRetrievalDecision(action="use_context")

    def fact_check_retrieval_decision(self) -> BaseModel:
        from annotation.workflow.models import FactCheckRetrievalDecision
        return FactCheckRetrievalDecision(action="use_context")

class OfflineFixtureProvider:
    provider = "offline"
    profile = "offline"

    def __init__(
        self,
        *,
        model: str = "offline-fixture",
        config_version: str = "offline-v1",
        structured_payload: Mapping[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.base_url = None
        self.config_version = config_version
        self.structured_payload = dict(structured_payload or {})
        self.capabilities = ProviderCapabilities(
            supports_structured_output=True,
            supports_streaming=True,
            supports_json_schema=True,
        )
        self.workflow = OfflineWorkflowCapabilities()

    def check_capability(self, capability: str) -> bool:
        return bool(getattr(self.capabilities, capability, False))

    capability_check = check_capability

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(
            text=f"Offline fixture response: {request.prompt}",
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
        schema_name = request.schema.__name__
        unit = request.context.get("unit")
        pack = request.context.get("context_pack")
        if not payload and schema_name == "ContentDraft" and unit is not None and pack is not None:
            payload = self.workflow.content_draft(unit, pack).model_dump(mode="python")
        elif not payload and schema_name == "ContentCritiqueDraft":
            payload = self.workflow.content_critique().model_dump(mode="python")
        elif not payload and schema_name == "RetrievalDecision" and unit is not None and pack is not None:
            payload = self.workflow.content_retrieval_decision(pack, unit).model_dump(mode="python")
            if not pack.excerpts and request.context.get("source_blocks"):
                payload["textbook_queries"] = [request.context["source_blocks"][0].text[:80]]
        elif not payload and schema_name == "FactCheckClaimsDraft":
            payload = {"claims": []}
        elif not payload and schema_name == "FactCheckRetrievalDecision":
            payload = self.workflow.fact_check_retrieval_decision().model_dump(mode="python")
        elif not payload and schema_name == "QuizRetrievalDecision":
            payload = self.workflow.quiz_retrieval_decision().model_dump(mode="python")
        elif not payload and schema_name == "QuizDraft" and unit is not None and pack is not None:
            payload = self.workflow.quiz_draft(unit, pack, request.context["task"].quiz_count).model_dump(mode="python")
        elif not payload and schema_name == "ContentTaskPlanDraft" and request.context.get("blueprint") is not None:
            payload = self.workflow.plan_content_tasks(request.context["blueprint"]).model_dump(mode="python")
        elif not payload and schema_name == "InteractiveComponentDraft" and request.context.get("task") is not None:
            payload = self.workflow.component_draft(request.context["task"]).model_dump(mode="python")
        elif not payload and schema_name == "InteractiveComponentCritiqueDraft":
            payload = self.workflow.component_critique().model_dump(mode="python")
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
            elif request.schema.__name__ == "ContentDraft":
                payload = {
                    "title": "知识单元讲解（离线回归）",
                    "content": "## 讲解\n\n这是按单个知识单元生成的离线回归讲解。实际教材事实由 ContextPack 中的来源片段支持。",
                    "callouts": [],
                    "source_refs": [],
                }
            elif request.schema.__name__ == "ContentTaskPlanDraft":
                payload = {"tasks": []}
        try:
            value = self.workflow.blueprint_draft(request.schema, payload)
        except Exception as exc:
            raise ProviderError(
                f"offline structured fixture does not match schema: {exc}",
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
        yield ModelEvent(type="token", text=f"Offline fixture response: {request.prompt}")
        yield ModelEvent(type="done")
