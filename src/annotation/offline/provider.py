"""Deterministic fixtures for offline development and regression runs."""

from __future__ import annotations

import json
import re
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

    def quiz_agent_step(self, unit: Any, pack: Any, requested_count: int | None) -> BaseModel:
        from annotation.workflow.models import QuizAgentStep
        return QuizAgentStep(action="write", draft=self.quiz_draft(unit, pack, requested_count))

    def component_draft(self, task: Any) -> BaseModel:
        return component_draft(task)

    def component_critique(self) -> BaseModel:
        from annotation.domain.artifacts import InteractiveComponentCritiqueDraft
        return InteractiveComponentCritiqueDraft()

    def content_retrieval_decision(self, pack: Any, unit: Any) -> BaseModel:
        from annotation.workflow.models import RetrievalDecision
        return RetrievalDecision(action="write") if pack.excerpts else RetrievalDecision(
            action="search", textbook_queries=[unit.title])

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
        if not payload and schema_name == "LocalBlueprintDraft":
            window = request.context["window"]
            payload = {"title": window.path[-1], "summary": "离线局部蓝图替身，不代表模型教材理解质量",
                "knowledge_units": [{"knowledge_unit_id": "local-unit", "title": window.path[-1],
                    "kind": "concept", "learning_objectives": ["理解本节教材内容"],
                    "source_refs": list(dict.fromkeys(span.source_ref for span in window.slices))}]}
        elif not payload and schema_name == "RelationBatchDecision":
            raise ProviderError("offline fixture requires explicit relationship decisions; it cannot classify unresolved concepts", category="schema")
        elif not payload and schema_name in {"CoordinationDecision", "FinalCoordinationDecision"}:
            groups = request.context["groups"]
            payload = {"action": "finalize", "group_order": [g["group_id"] for g in groups],
                "summary": "离线协调替身，保持教材顺序"}
        elif not payload and schema_name == "ContentAgentStep" and unit is not None and pack is not None:
            if pack.excerpts:
                payload = {"action":"write", "draft":self.workflow.content_draft(unit,pack).model_dump(mode="python")}
            else:
                payload = {"action":"search", "textbook_queries":[request.context['source_blocks'][0].text[:80]
                    if request.context.get('source_blocks') else unit.title]}
        elif not payload and schema_name == "ContentDraft" and unit is not None and pack is not None:
            payload = self.workflow.content_draft(unit, pack).model_dump(mode="python")
        elif not payload and schema_name == "ContentCritiqueDraft":
            payload = self.workflow.content_critique().model_dump(mode="python")
        elif not payload and schema_name == "RetrievalDecision" and unit is not None and pack is not None:
            payload = self.workflow.content_retrieval_decision(pack, unit).model_dump(mode="python")
            if not pack.excerpts and request.context.get("source_blocks"):
                payload["textbook_queries"] = [request.context["source_blocks"][0].text[:80]]
        elif not payload and schema_name == "FactCheckClaimsDraft":
            payload = {"claims": []}
        elif not payload and schema_name == "FactCheckRetrievalPlan":
            claim_ids = list(dict.fromkeys(re.findall(r'"claim_id"\s*:\s*"([^"]+)"', request.prompt)))
            decision = self.workflow.fact_check_retrieval_decision().model_dump(mode="python")
            payload = {"decisions": [{"claim_id": claim_id, **decision} for claim_id in claim_ids]}
        elif not payload and schema_name == "FactCheckRetrievalDecision":
            payload = self.workflow.fact_check_retrieval_decision().model_dump(mode="python")
        elif not payload and schema_name == "QuizAgentStep" and unit is not None and pack is not None:
            payload = self.workflow.quiz_agent_step(
                unit, pack, request.context["task"].quiz_count
            ).model_dump(mode="python")
        elif not payload and schema_name == "ContentTaskPlanDraft" and request.context.get("blueprint") is not None:
            payload = self.workflow.plan_content_tasks(request.context["blueprint"]).model_dump(mode="python")
        elif not payload and schema_name == "InteractiveComponentDraft" and request.context.get("task") is not None:
            payload = self.workflow.component_draft(request.context["task"]).model_dump(mode="python")
        elif not payload and schema_name == "InteractiveComponentCritiqueDraft":
            payload = self.workflow.component_critique().model_dump(mode="python")
        if not payload:
            if request.schema.__name__ == "ContentDraft":
                payload = {
                    "title": "知识单元讲解（离线回归）",
                    "content": "## 讲解\n\n这是按单个知识单元生成的离线回归讲解。实际教材事实由 ContextPack 中的来源片段支持。",
                    "callouts": [],
                    "source_refs": [],
                }
            elif request.schema.__name__ == "ContentTaskPlanDraft":
                payload = {"tasks": []}
        try:
            value = request.schema.model_validate(payload)
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
