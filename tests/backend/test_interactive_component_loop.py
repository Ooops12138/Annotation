from __future__ import annotations

import json
from typing import Any, Callable

from annotation.domain.artifacts import (
    ComponentAccessibility,
    ContentArtifact,
    ContentTask,
    ContextExcerpt,
    ContextPack,
    FunctionDomainSpec,
    FunctionGraphSpec,
    InteractiveComponentArtifact,
    InteractiveComponentPlan,
    InteractiveComponentSandboxReport,
    InteractiveComponentTask,
    KnowledgeUnit,
)
from annotation.providers.models import ProviderCapabilities, ProviderError, StructuredGenerationRequest, StructuredGenerationResponse
from annotation.workflow.interactive_components import build_interactive_component_subgraph, interactive_component_hard_check
from annotation.workflow.persistence import write_interactive_component_artifact, write_run_manifest


class ScriptedComponentProvider:
    provider = "scripted"
    model = "scripted-component-model"
    base_url = None
    config_version = "test-v1"
    capabilities = ProviderCapabilities(supports_structured_output=True)

    def __init__(self, responses: list[dict[str, Any] | ProviderError | Callable[[StructuredGenerationRequest[Any]], dict[str, Any]]]) -> None:
        self.responses = list(responses)
        self.calls: list[StructuredGenerationRequest[Any]] = []

    def generate_structured(self, request: StructuredGenerationRequest[Any]) -> StructuredGenerationResponse[Any]:
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, ProviderError):
            raise response
        if callable(response):
            response = response(request)
        value = request.schema.model_validate(response)
        return StructuredGenerationResponse(
            value=value,
            raw_text=value.model_dump_json(),
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            config_version=self.config_version,
            duration_ms=0,
            usage={"total_tokens": 1},
        )


def _state() -> dict[str, Any]:
    run_id = "interactive-component-test"
    unit = KnowledgeUnit(
        artifact_id="unit-supremum",
        run_id=run_id,
        version=1,
        status="accepted",
        source_refs=["src-1"],
        created_by="test",
        title="上确界",
        kind="concept",
        learning_objectives=["区分上确界与最大元"],
    )
    content_task = ContentTask(
        task_id="content-task-1",
        run_id=run_id,
        blueprint_version="blueprint:v1",
        knowledge_unit_id=unit.artifact_id,
        source_refs=["src-1"],
    )
    context_pack = ContextPack(
        context_pack_id="ctx-supremum",
        run_id=run_id,
        task_id=content_task.task_id,
        knowledge_unit_id=unit.artifact_id,
        source_refs=["src-1"],
        selected_source_refs=["src-1"],
        excerpts=[ContextExcerpt(source_ref="src-1", page_number=1, block_index=0, text="集合 [0,1) 的上确界是 1，且没有最大元。")],
        estimated_input_tokens=20,
        input_budget_tokens=100,
    )
    content = ContentArtifact(
        artifact_id="content-supremum",
        run_id=run_id,
        version=1,
        status="accepted",
        source_refs=["src-1"],
        created_by="test",
        knowledge_unit_ids=[unit.artifact_id],
        task_id=content_task.task_id,
        context_pack_id=context_pack.context_pack_id,
        title=unit.title,
        content="集合 [0,1) 的上确界是 1，且不存在最大元。",
    )
    plan = InteractiveComponentPlan(
        plan_id="interactive-plan-1",
        run_id=run_id,
        status="planned",
        knowledge_unit_id=unit.artifact_id,
        accepted_content_artifact_id=content.artifact_id,
        source_refs=["src-1"],
        reason="数轴能帮助观察开端点。",
    )
    task = InteractiveComponentTask(
        task_id="interactive-task-1",
        plan_id=plan.plan_id,
        run_id=run_id,
        knowledge_unit_id=unit.artifact_id,
        accepted_content_artifact_id=content.artifact_id,
        context_pack_id=context_pack.context_pack_id,
        source_refs=["src-1"],
    )
    return {
        "run_id": run_id,
        "plan": plan,
        "task": task,
        "unit": unit,
        "content_artifact": content,
        "context_pack": context_pack,
        "valid_source_refs": ["src-1"],
        "max_attempts": 3,
        "attempts": [],
    }


def _interval_draft(request: StructuredGenerationRequest[Any], *, refs: list[str] | None = None) -> dict[str, Any]:
    return {
        "task_id": request.metadata["task_id"],
        "knowledge_unit_id": request.metadata["knowledge_unit_id"],
        "context_pack_id": request.metadata["context_pack_id"],
        "spec": {
            "component_type": "interval_line",
            "component_id": "interval-supremum",
            "title": "观察区间 [0,1)",
            "learning_objective": "区分上确界 1 与不存在的最大元。",
            "source_refs": refs or ["src-1"],
            "accessibility": {
                "aria_label": "区间 [0,1) 数轴",
                "description": "右端点为空心圆。",
                "observation": "1 是上确界，集合没有最大元。",
            },
            "controls": [{"kind": "toggle", "control_id": "show-supremum", "label": "标出上确界", "default_value": False}],
            "test_actions": [{"action": "toggle", "control_id": "show-supremum", "value": True, "expected_text": "上确界"}],
            "annotations": [],
            "interval_start": 0,
            "interval_end": 1,
            "left_endpoint": "closed",
            "right_endpoint": "open",
            "supremum": 1,
            "maximum": None,
        },
    }


def _sandbox_ok(*_args: Any, **_kwargs: Any) -> InteractiveComponentSandboxReport:
    return InteractiveComponentSandboxReport(
        status="passed",
        dom_snapshot="<main>上确界</main>",
        accessibility_snapshot="- main: 上确界",
        network_blocked=True,
        screenshot_path="sandbox.png",
    )


def test_valid_component_is_accepted_after_shared_sandbox_and_critic() -> None:
    provider = ScriptedComponentProvider([_interval_draft, {"issues": []}])

    result = build_interactive_component_subgraph(provider, max_attempts=3, sandbox_runner=_sandbox_ok).invoke(_state())

    trace = result["interactive_component_loop_trace"]
    assert trace.final_status == "accepted"
    assert trace.attempts[0].sandbox_report.status == "passed"
    assert result["final_artifact"].status == "accepted"
    assert [call.schema.__name__ for call in provider.calls] == ["InteractiveComponentDraft", "InteractiveComponentCritiqueDraft"]


def test_invalid_source_revises_before_browser_or_critic() -> None:
    provider = ScriptedComponentProvider([
        lambda request: _interval_draft(request, refs=["unknown-source"]),
        _interval_draft,
        {"issues": []},
    ])

    result = build_interactive_component_subgraph(provider, max_attempts=3, sandbox_runner=_sandbox_ok).invoke(_state())

    trace = result["interactive_component_loop_trace"]
    assert [attempt.route for attempt in trace.attempts] == ["revise", "accept"]
    assert trace.attempts[0].sandbox_report is None
    assert trace.attempts[0].critic_status == "skipped"
    assert trace.attempts[1].generation_stage == "revision"


def test_not_needed_decision_is_preserved_in_trace() -> None:
    provider = ScriptedComponentProvider([
        lambda request: {
            "task_id": request.metadata["task_id"],
            "knowledge_unit_id": request.metadata["knowledge_unit_id"],
            "context_pack_id": request.metadata["context_pack_id"],
            "spec": None,
            "not_needed_reason": "当前概念用静态讲解更清楚。",
        },
    ])

    result = build_interactive_component_subgraph(provider, max_attempts=3, sandbox_runner=_sandbox_ok).invoke(_state())

    trace = result["interactive_component_loop_trace"]
    assert trace.final_status == "not_needed"
    assert trace.plan.status == "not_needed"
    assert "静态讲解" in trace.plan.reason
    assert trace.attempts[0].route == "not_needed"


def test_browser_infrastructure_error_fails_without_silent_degradation() -> None:
    provider = ScriptedComponentProvider([_interval_draft])

    result = build_interactive_component_subgraph(
        provider,
        max_attempts=3,
        sandbox_runner=lambda *_args, **_kwargs: InteractiveComponentSandboxReport(status="browser_error", error="Chromium missing"),
    ).invoke(_state())

    trace = result["interactive_component_loop_trace"]
    assert trace.final_status == "failed"
    assert trace.stop_reason == "browser_infrastructure_error"
    assert len(trace.attempts) == 1


def test_function_domain_probes_reject_sqrt_and_unexcluded_hole() -> None:
    state = _state()
    task = state["task"]
    pack = state["context_pack"]
    common = {
        "component_id": "function-test",
        "title": "函数图像",
        "learning_objective": "观察函数定义域。",
        "source_refs": ["src-1"],
        "accessibility": ComponentAccessibility(aria_label="函数图像", description="定义域图像", observation="观察定义域。"),
        "controls": [],
        "test_actions": [],
        "annotations": [],
    }
    sqrt_spec = FunctionGraphSpec(
        **common,
        formula="sqrt(x-2)",
        domain=FunctionDomainSpec(start=0, end=4),
        sample_points=[0, 2, 4],
    )
    hole_spec = FunctionGraphSpec(
        **common,
        formula="1/(x-1)",
        domain=FunctionDomainSpec(start=0, end=2),
        sample_points=[0, 1, 2],
    )
    for spec in (sqrt_spec, hole_spec):
        artifact = InteractiveComponentArtifact(
            artifact_id=f"artifact-{spec.component_id}-{spec.formula[0]}",
            run_id=task.run_id,
            version=1,
            status="checking",
            source_refs=["src-1"],
            created_by="test",
            plan_id=task.plan_id,
            task_id=task.task_id,
            knowledge_unit_id=task.knowledge_unit_id,
            accepted_content_artifact_id=task.accepted_content_artifact_id,
            context_pack_id=task.context_pack_id,
            spec=spec,
        )
        check = interactive_component_hard_check(
            task=task,
            context_pack=pack,
            valid_source_refs={"src-1"},
            candidate_artifact=artifact,
        )
        assert check.status == "needs_revision"
        assert check.issues[0].category == "formula"


def test_component_snapshot_keeps_full_trace_but_manifest_only_has_summary(tmp_path) -> None:
    state = _state()
    plan = state["plan"]
    state.update({
        "interactive_component_plan": plan,
        "interactive_component_task": state["task"],
        "interactive_component_artifacts": [],
        "interactive_component_status": "not_needed",
        "interactive_component_summary": {"component_count": 0, "attempt_count": 0, "final_status": "not_needed", "stop_reason": "not_needed"},
    })
    result = build_interactive_component_subgraph(
        ScriptedComponentProvider([
            lambda request: {
                "task_id": request.metadata["task_id"], "knowledge_unit_id": request.metadata["knowledge_unit_id"],
                "context_pack_id": request.metadata["context_pack_id"], "spec": None, "not_needed_reason": "不需要组件。",
            },
        ]),
        max_attempts=3,
        sandbox_runner=_sandbox_ok,
    ).invoke(state)
    state["interactive_component_loop_trace"] = result["interactive_component_loop_trace"]
    path = write_interactive_component_artifact(state, root=tmp_path / "components")
    assert path is not None
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == "interactive-components-v1"
    assert snapshot["loop_trace"]["final_status"] == "not_needed"
    state["interactive_component_artifact_path"] = str(path.resolve())
    manifest_path = write_run_manifest(state, blueprint_path=None, document_path=None, root=tmp_path / "runs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "run-manifest-v4"
    assert manifest["interactive_component_summary"]["final_status"] == "not_needed"
    assert "loop_trace" not in manifest["interactive_component_summary"]
