"""A-004 bounded generation loop for safe, data-only learning components."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterable
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from annotation.domain.artifacts import (
    ComponentAccessibility,
    ComponentTestAction,
    ContentArtifact,
    ContentTask,
    ContextPack,
    InteractiveComponentArtifact,
    InteractiveComponentAttemptTrace,
    InteractiveComponentCriticIssueDraft,
    InteractiveComponentCritiqueDraft,
    InteractiveComponentHardCheckResult,
    InteractiveComponentLoopTrace,
    InteractiveComponentPlan,
    InteractiveComponentSandboxReport,
    InteractiveComponentTask,
    IntervalLineSpec,
    KnowledgeUnit,
    ReviewIssue,
    ToggleControlSpec,
)
from annotation.prompt_loader import load_prompt
from annotation.providers import ModelProvider, ProviderError, StructuredGenerationRequest
from annotation.workflow.content import render_context_pack
from annotation.workflow.content_support import _unit_context
from annotation.workflow.graph import _metadata, _provider_metadata
from annotation.workflow.interactive_expression import function_domain_probe_errors
from annotation.workflow.interactive_sandbox import run_interactive_component_sandbox
from annotation.workflow.models import InteractiveComponentDraft, InteractiveComponentState


SandboxRunner = Callable[..., InteractiveComponentSandboxReport]


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _dedupe_issues(issues: Iterable[ReviewIssue]) -> list[ReviewIssue]:
    seen: set[str] = set()
    result: list[ReviewIssue] = []
    for issue in issues:
        if issue.issue_id not in seen:
            result.append(issue)
            seen.add(issue.issue_id)
    return result


def _issue(
    *,
    task: InteractiveComponentTask,
    suffix: str,
    category: Literal["fact", "logic", "formula", "coverage", "source", "transition", "conflict", "uncertainty", "stance"],
    severity: Literal["info", "warning", "blocking"],
    message: str,
    target_id: str | None = None,
    source_refs: Iterable[str] = (),
    suggested_action: str | None = None,
) -> ReviewIssue:
    return ReviewIssue(
        issue_id=f"interactive-component-{task.task_id}-{suffix}",
        category=category,
        severity=severity,
        layer="structure",
        message=message,
        target_id=target_id or task.knowledge_unit_id,
        source_refs=_unique(source_refs),
        suggested_action=suggested_action,
    )


def _component_score(unit: KnowledgeUnit, artifact: ContentArtifact) -> int:
    text = " ".join([unit.title, *unit.learning_objectives, artifact.content]).lower()
    score = 0
    for token in ("上确界", "区间", "函数", "复数", "数轴", "supremum", "interval", "function", "complex"):
        if token in text:
            score += 1
    return score


def plan_interactive_component(
    *,
    run_id: str,
    tasks: list[ContentTask],
    context_packs: list[ContextPack],
    content_artifacts: list[ContentArtifact],
    units: list[KnowledgeUnit],
) -> tuple[InteractiveComponentPlan, InteractiveComponentTask | None, KnowledgeUnit | None, ContentArtifact | None, ContextPack | None]:
    """Pick one accepted, source-bound unit deterministically before generation.

    The model can still return ``not_needed``. Selection itself is deliberately
    deterministic so A-004 does not add an unbounded planner call.
    """

    tasks_by_id = {task.task_id: task for task in tasks}
    packs_by_task = {pack.task_id: pack for pack in context_packs}
    units_by_id = {unit.artifact_id: unit for unit in units}
    candidates: list[tuple[int, int, ContentTask, ContextPack, KnowledgeUnit, ContentArtifact, list[str]]] = []
    for artifact in content_artifacts:
        if artifact.status != "accepted" or not artifact.task_id:
            continue
        task = tasks_by_id.get(artifact.task_id)
        if task is not None and task.interactive_component_policy == "skip":
            continue
        pack = packs_by_task.get(artifact.task_id)
        unit = units_by_id.get(task.knowledge_unit_id) if task is not None else None
        if task is None or pack is None or unit is None:
            continue
        refs = _unique(ref for ref in artifact.source_refs if ref in set(pack.source_refs))
        if not refs:
            continue
        priority = 1 if task.interactive_component_policy == "required" else 0
        candidates.append((priority, _component_score(unit, artifact), task, pack, unit, artifact, refs))
    if not candidates:
        plan = InteractiveComponentPlan(
            plan_id=f"interactive-plan-{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            status="not_needed",
            reason="没有同时具备已接受讲解、ContextPack 和教材来源的知识单元，未生成交互组件。",
        )
        return plan, None, None, None, None
    # Stable tie-breaking makes test runs auditable and repeatable.
    _, _, content_task, pack, unit, artifact, refs = sorted(
        candidates,
        key=lambda item: (-item[0], -item[1], item[3].task_id, item[5].artifact_id),
    )[0]
    plan = InteractiveComponentPlan(
        plan_id=f"interactive-plan-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        status="planned",
        knowledge_unit_id=unit.artifact_id,
        accepted_content_artifact_id=artifact.artifact_id,
        source_refs=refs,
        reason=(
            "课程架构师要求该知识单元生成交互式组件。"
            if content_task.interactive_component_policy == "required"
            else "选择来源完整且适合用图形观察的已接受知识单元。"
        ),
    )
    task = InteractiveComponentTask(
        task_id=f"interactive-task-{uuid.uuid4().hex[:12]}",
        plan_id=plan.plan_id,
        run_id=run_id,
        knowledge_unit_id=unit.artifact_id,
        accepted_content_artifact_id=artifact.artifact_id,
        context_pack_id=pack.context_pack_id,
        source_refs=refs,
        acceptance_criteria=[
            "只输出允许的结构化 JSXGraph 规格，不输出可执行前端代码。",
            "规格必须能用当前教材来源解释，并帮助初学者观察学习目标。",
            "函数图像必须声明受限公式、定义域、端点、采样点和排除点。",
        ],
    )
    return plan, task, unit, artifact, pack


def _mock_draft(task: InteractiveComponentTask) -> InteractiveComponentDraft:
    """Deterministic interval fixture used only by the built-in mock provider."""

    spec = IntervalLineSpec(
        component_id="interval-supremum-line",
        title="观察区间的右端点与上确界",
        learning_objective="区分区间 [0,1) 的上确界 1 与不存在的最大元。",
        source_refs=list(task.source_refs),
        accessibility=ComponentAccessibility(
            aria_label="区间 [0,1) 的数轴图示",
            description="数轴显示从 0 到 1 的区间，右端点为空心圆，因此 1 不属于该集合。",
            observation="右端点 1 是上确界，但由于端点开放，集合没有最大元。",
        ),
        controls=[ToggleControlSpec(
            control_id="show-supremum",
            label="标出上确界",
            default_value=False,
        )],
        test_actions=[ComponentTestAction(
            action="toggle",
            control_id="show-supremum",
            value=True,
            expected_text="上确界",
        )],
        annotations=[],
        interval_start=0,
        interval_end=1,
        left_endpoint="closed",
        right_endpoint="open",
        supremum=1,
        maximum=None,
    )
    return InteractiveComponentDraft(
        task_id=task.task_id,
        knowledge_unit_id=task.knowledge_unit_id,
        context_pack_id=task.context_pack_id,
        spec=spec,
    )


def _artifact_from_draft(
    *,
    draft: InteractiveComponentDraft,
    task: InteractiveComponentTask,
    provider_name: str,
    attempt: int,
    fixture_adaptation: bool,
    prompt_version: str,
) -> InteractiveComponentArtifact | None:
    if draft.spec is None:
        return None
    return InteractiveComponentArtifact(
        artifact_id=f"interactive-component-{uuid.uuid4().hex[:12]}",
        run_id=task.run_id,
        version=max(1, attempt),
        status="checking",
        source_refs=list(draft.spec.source_refs),
        created_by=f"provider:{provider_name}",
        plan_id=task.plan_id,
        task_id=task.task_id,
        knowledge_unit_id=task.knowledge_unit_id,
        accepted_content_artifact_id=task.accepted_content_artifact_id,
        context_pack_id=task.context_pack_id,
        spec=draft.spec,
        prompt_version=prompt_version,
        metadata={"attempt": attempt, "fixture_adaptation": fixture_adaptation},
    )


def interactive_component_hard_check(
    *,
    task: InteractiveComponentTask,
    context_pack: ContextPack,
    valid_source_refs: set[str],
    candidate_artifact: InteractiveComponentArtifact | None,
) -> InteractiveComponentHardCheckResult:
    """Reject unbound, unsafe, or mathematically undefined component specs."""

    if candidate_artifact is None:
        return InteractiveComponentHardCheckResult(status="needs_revision")
    issues: list[ReviewIssue] = []
    candidate = candidate_artifact
    if candidate.run_id != task.run_id:
        issues.append(_issue(task=task, suffix="run-binding", category="coverage", severity="blocking", message="组件候选没有绑定当前运行。", target_id=candidate.artifact_id))
    if candidate.task_id != task.task_id or candidate.plan_id != task.plan_id:
        issues.append(_issue(task=task, suffix="task-binding", category="coverage", severity="blocking", message="组件候选没有绑定当前组件任务或计划。", target_id=candidate.artifact_id))
    if candidate.knowledge_unit_id != task.knowledge_unit_id or candidate.accepted_content_artifact_id != task.accepted_content_artifact_id:
        issues.append(_issue(task=task, suffix="content-binding", category="coverage", severity="blocking", message="组件候选没有绑定当前知识单元和已接受讲解。", target_id=candidate.artifact_id))
    if candidate.context_pack_id != task.context_pack_id:
        issues.append(_issue(task=task, suffix="context-binding", category="coverage", severity="blocking", message="组件候选没有绑定当前 ContextPack。", target_id=candidate.artifact_id))
    if candidate.spec.component_type not in task.allowed_component_types:
        issues.append(_issue(task=task, suffix="component-type", category="coverage", severity="blocking", message="组件类型不在任务允许列表内。", target_id=candidate.artifact_id))
    spec_refs = _unique(candidate.spec.source_refs)
    allowed_refs = set(task.source_refs) & set(context_pack.source_refs) & valid_source_refs
    if not spec_refs:
        issues.append(_issue(task=task, suffix="missing-sources", category="source", severity="blocking", message="组件规格缺少教材来源。", target_id=candidate.artifact_id))
    elif not set(spec_refs).issubset(allowed_refs):
        issues.append(_issue(
            task=task,
            suffix="invalid-sources",
            category="source",
            severity="blocking",
            message="组件规格包含当前任务、ContextPack 或教材导入范围之外的来源。",
            target_id=candidate.artifact_id,
            source_refs=[ref for ref in spec_refs if ref not in allowed_refs],
        ))
    if set(candidate.source_refs) != set(spec_refs):
        issues.append(_issue(task=task, suffix="artifact-sources", category="source", severity="blocking", message="组件 artifact 来源与不可变规格不一致。", target_id=candidate.artifact_id))
    if candidate.spec.component_type == "function_graph":
        errors = function_domain_probe_errors(
            candidate.spec.formula,
            domain_start=candidate.spec.domain.start,
            domain_end=candidate.spec.domain.end,
            sample_points=list(candidate.spec.sample_points),
            excluded_points=list(candidate.spec.excluded_points),
        )
        if errors:
            issues.append(_issue(
                task=task,
                suffix="function-domain",
                category="formula",
                severity="blocking",
                message="；".join(errors),
                target_id=candidate.artifact_id,
                source_refs=spec_refs,
                suggested_action="将公式改为受限表达式，并使定义域、采样点和排除点与函数一致。",
            ))
        else:
            candidate.metadata["formula_ast"] = "validated"
    return InteractiveComponentHardCheckResult(
        status="accepted" if not issues else "needs_revision",
        issues=issues,
        checked_artifact_id=candidate.artifact_id,
    )


def _critic_issues(
    critique: InteractiveComponentCritiqueDraft,
    *,
    task: InteractiveComponentTask,
) -> list[ReviewIssue]:
    categories: dict[str, Literal["fact", "logic", "formula", "coverage", "source"]] = {
        "objective_mismatch": "coverage",
        "source_mismatch": "source",
        "mathematical_mismatch": "formula",
        "interaction_mismatch": "logic",
        "accessibility": "coverage",
    }
    blocking = {"objective_mismatch", "source_mismatch", "mathematical_mismatch", "interaction_mismatch"}
    result: list[ReviewIssue] = []
    for index, item in enumerate(critique.issues, start=1):
        result.append(_issue(
            task=task,
            suffix=f"critic-{index}-{item.code}",
            category=categories[item.code],
            severity="blocking" if item.code in blocking else "warning",
            message=item.message,
            suggested_action=item.suggested_action,
        ))
    return result


def _mock_metadata(provider: ModelProvider, agent: str) -> dict[str, Any]:
    return {
        "agent": agent,
        "provider": getattr(provider, "provider", "mock"),
        "model": getattr(provider, "model", "fixture-model"),
        "base_url": getattr(provider, "base_url", None),
        "config_version": getattr(provider, "config_version", "mock-v1"),
        "duration_ms": 0,
        "usage": {},
        "fixture_adaptation": True,
    }


def build_interactive_component_subgraph(
    provider: ModelProvider,
    *,
    max_attempts: int,
    sandbox_runner: SandboxRunner = run_interactive_component_sandbox,
):
    """Build the A-004 per-component LangGraph subgraph."""

    provider_name = str(getattr(provider, "provider", "unknown"))

    def output_limit(default: int) -> int:
        limit = getattr(getattr(provider, "capabilities", None), "max_output_tokens", None)
        return min(default, int(limit)) if limit else default

    def generate_candidate(state: InteractiveComponentState) -> dict[str, Any]:
        attempt = int(state.get("attempt", 0)) + 1
        task = state["task"]
        unit = state["unit"]
        pack = state["context_pack"]
        previous = state.get("candidate_artifact")
        revision = attempt > 1
        if revision:
            prompt = load_prompt(
                "revise_interactive_component",
                KNOWLEDGE_UNIT_CONTEXT=_unit_context(unit),
                ACCEPTED_CONTENT=json.dumps(state["content_artifact"].model_dump(mode="json"), ensure_ascii=False, indent=2),
                CONTEXT_PACK=render_context_pack(pack),
                COMPONENT_TASK=json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2),
                CANDIDATE_ARTIFACT=json.dumps(previous.model_dump(mode="json") if previous is not None else {}, ensure_ascii=False, indent=2),
                HARD_CHECK_RESULT=json.dumps(state.get("hard_check").model_dump(mode="json") if state.get("hard_check") else {}, ensure_ascii=False, indent=2),
                SANDBOX_REPORT=json.dumps(state.get("sandbox_report").model_dump(mode="json") if state.get("sandbox_report") else {}, ensure_ascii=False, indent=2),
                CRITIQUE_RESULT=json.dumps(state.get("critic_parsed_output") or {"issues": []}, ensure_ascii=False, indent=2),
            )
            agent, prompt_version = "revise_interactive_component", "revise_interactive_component:v1"
        else:
            prompt = load_prompt(
                "generate_interactive_component",
                KNOWLEDGE_UNIT_CONTEXT=_unit_context(unit),
                ACCEPTED_CONTENT=json.dumps(state["content_artifact"].model_dump(mode="json"), ensure_ascii=False, indent=2),
                CONTEXT_PACK=render_context_pack(pack),
                COMPONENT_TASK=json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2),
            )
            agent, prompt_version = "generate_interactive_component", "generate_interactive_component:v1"
        result: dict[str, Any] = {
            "attempt": attempt,
            "generation_stage": "revision" if revision else "initial",
            "generation_prompt": prompt,
            "revision_prompt": prompt if revision else None,
            "generation_raw_output": "",
            "generation_parsed_output": None,
            "generation_status": "provider_error",
            "generation_error": None,
            "generation_error_category": None,
            "generation_retryable": False,
            "generation_provider_metadata": {},
            "candidate_draft": None,
            "candidate_artifact": None,
            "hard_check": None,
            "sandbox_report": None,
            "critic_prompt": None,
            "critic_raw_output": "",
            "critic_parsed_output": None,
            "critic_status": "skipped",
            "critic_error": None,
            "critic_error_category": None,
            "critic_retryable": False,
            "critic_provider_metadata": {},
            "critic_feedback": [],
        }
        try:
            if provider_name == "mock":
                draft = _mock_draft(task)
                metadata = _mock_metadata(provider, agent)
                raw_output = draft.model_dump_json()
                fixture_adaptation = True
            else:
                response = provider.generate_structured(StructuredGenerationRequest(
                    prompt=prompt,
                    schema=InteractiveComponentDraft,
                    max_output_tokens=output_limit(2800),
                    metadata={
                        "agent": agent,
                        "run_id": state["run_id"],
                        "task_id": task.task_id,
                        "knowledge_unit_id": unit.artifact_id,
                        "context_pack_id": pack.context_pack_id,
                        "attempt": attempt,
                    },
                ))
                draft = InteractiveComponentDraft.model_validate(response.value.model_dump(mode="python"))
                metadata = _metadata(response)
                metadata["agent"] = agent
                raw_output = response.raw_text
                fixture_adaptation = False
            result.update({
                "generation_status": "succeeded",
                "generation_raw_output": raw_output,
                "generation_parsed_output": draft.model_dump(mode="json"),
                "generation_provider_metadata": metadata,
                "candidate_draft": draft,
                "candidate_artifact": _artifact_from_draft(
                    draft=draft,
                    task=task,
                    provider_name=provider_name,
                    attempt=attempt,
                    fixture_adaptation=fixture_adaptation,
                    prompt_version=prompt_version,
                ),
            })
        except ProviderError as exc:
            category = getattr(exc, "category", "provider")
            parsed = getattr(exc, "parsed_output", None)
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump(mode="json")
            elif not isinstance(parsed, dict):
                parsed = None
            result.update({
                "generation_status": "schema_error" if category == "schema" else "provider_error",
                "generation_raw_output": str(getattr(exc, "raw_output", "") or ""),
                "generation_parsed_output": parsed,
                "generation_error": str(exc),
                "generation_error_category": category,
                "generation_retryable": bool(getattr(exc, "retryable", False) or category == "schema"),
                "generation_provider_metadata": {**_provider_metadata(provider), "agent": agent},
            })
        except Exception as exc:
            result.update({
                "generation_status": "provider_error",
                "generation_error": str(exc),
                "generation_error_category": "runtime",
                "generation_provider_metadata": {**_provider_metadata(provider), "agent": agent},
            })
        return result

    def hard_check_candidate(state: InteractiveComponentState) -> dict[str, Any]:
        if state.get("generation_status") != "succeeded" or state.get("candidate_artifact") is None:
            return {"hard_check": None}
        return {
            "hard_check": interactive_component_hard_check(
                task=state["task"],
                context_pack=state["context_pack"],
                valid_source_refs=set(state.get("valid_source_refs", [])),
                candidate_artifact=state.get("candidate_artifact"),
            )
        }

    def after_hard_check(state: InteractiveComponentState) -> str:
        if state.get("candidate_artifact") is None:
            return "route"
        check = state.get("hard_check")
        return "sandbox" if check is not None and check.status == "accepted" else "route"

    def sandbox_candidate(state: InteractiveComponentState) -> dict[str, Any]:
        candidate = state.get("candidate_artifact")
        if candidate is None:
            return {"sandbox_report": None}
        try:
            report = sandbox_runner(
                candidate.spec,
                run_id=state["run_id"],
                task_id=state["task"].task_id,
                attempt=int(state["attempt"]),
            )
        except Exception as exc:
            report = InteractiveComponentSandboxReport(status="browser_error", error=str(exc))
        return {"sandbox_report": report}

    def after_sandbox(state: InteractiveComponentState) -> str:
        report = state.get("sandbox_report")
        return "critic" if report is not None and report.status == "passed" else "route"

    def critic_candidate(state: InteractiveComponentState) -> dict[str, Any]:
        task = state["task"]
        candidate = state.get("candidate_artifact")
        prompt = load_prompt(
            "critique_interactive_component",
            KNOWLEDGE_UNIT_CONTEXT=_unit_context(state["unit"]),
            ACCEPTED_CONTENT=json.dumps(state["content_artifact"].model_dump(mode="json"), ensure_ascii=False, indent=2),
            CONTEXT_PACK=render_context_pack(state["context_pack"]),
            COMPONENT_TASK=json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2),
            COMPONENT_SPEC=json.dumps(candidate.spec.model_dump(mode="json") if candidate is not None else {}, ensure_ascii=False, indent=2),
            SANDBOX_REPORT=json.dumps(state.get("sandbox_report").model_dump(mode="json") if state.get("sandbox_report") else {}, ensure_ascii=False, indent=2),
        )
        result: dict[str, Any] = {
            "critic_prompt": prompt,
            "critic_raw_output": "",
            "critic_parsed_output": None,
            "critic_status": "provider_error",
            "critic_error": None,
            "critic_error_category": None,
            "critic_retryable": False,
            "critic_provider_metadata": {},
            "critic_feedback": [],
        }
        try:
            if provider_name == "mock":
                critique = InteractiveComponentCritiqueDraft()
                metadata = _mock_metadata(provider, "critique_interactive_component")
                raw_output = critique.model_dump_json()
            else:
                response = provider.generate_structured(StructuredGenerationRequest(
                    prompt=prompt,
                    schema=InteractiveComponentCritiqueDraft,
                    max_output_tokens=output_limit(1400),
                    metadata={
                        "agent": "critique_interactive_component",
                        "run_id": state["run_id"],
                        "task_id": task.task_id,
                        "knowledge_unit_id": task.knowledge_unit_id,
                        "context_pack_id": task.context_pack_id,
                        "attempt": state["attempt"],
                    },
                ))
                critique = InteractiveComponentCritiqueDraft.model_validate(response.value.model_dump(mode="python"))
                metadata = _metadata(response)
                metadata["agent"] = "critique_interactive_component"
                raw_output = response.raw_text
            result.update({
                "critic_status": "succeeded",
                "critic_raw_output": raw_output,
                "critic_parsed_output": critique.model_dump(mode="json"),
                "critic_provider_metadata": metadata,
                "critic_feedback": _critic_issues(critique, task=task),
            })
        except ProviderError as exc:
            category = getattr(exc, "category", "provider")
            parsed = getattr(exc, "parsed_output", None)
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump(mode="json")
            elif not isinstance(parsed, dict):
                parsed = None
            result.update({
                "critic_status": "schema_error" if category == "schema" else "provider_error",
                "critic_raw_output": str(getattr(exc, "raw_output", "") or ""),
                "critic_parsed_output": parsed,
                "critic_error": str(exc),
                "critic_error_category": category,
                "critic_retryable": bool(getattr(exc, "retryable", False) or category == "schema"),
                "critic_provider_metadata": {**_provider_metadata(provider), "agent": "critique_interactive_component"},
            })
        except Exception as exc:
            result.update({
                "critic_status": "provider_error",
                "critic_error": str(exc),
                "critic_error_category": "runtime",
                "critic_provider_metadata": {**_provider_metadata(provider), "agent": "critique_interactive_component"},
            })
        return result

    def route_candidate(state: InteractiveComponentState) -> dict[str, Any]:
        attempt = int(state.get("attempt", 0))
        task = state["task"]
        candidate = state.get("candidate_artifact")
        draft = state.get("candidate_draft")
        generation_status = state.get("generation_status", "provider_error")
        critic_status = state.get("critic_status", "skipped")
        hard_check = state.get("hard_check")
        sandbox = state.get("sandbox_report")
        feedback = list(getattr(hard_check, "issues", []) or []) + list(state.get("critic_feedback", []))

        if generation_status == "succeeded" and candidate is None and draft is not None and draft.spec is None:
            state["plan"].status = "not_needed"
            state["plan"].reason = draft.not_needed_reason or "当前内容不适合交互组件。"
            task.status = "not_needed"
            route, stop_reason, final_status = "not_needed", "not_needed", "not_needed"
        else:
            non_retryable_provider_error = (
                generation_status == "provider_error" and not state.get("generation_retryable")
            ) or (
                critic_status == "provider_error" and not state.get("critic_retryable")
            )
            browser_infrastructure_error = sandbox is not None and sandbox.status == "browser_error"
            critic_blocks = any(issue.severity == "blocking" for issue in state.get("critic_feedback", []))
            needs_revision = (
                generation_status != "succeeded"
                or candidate is None
                or hard_check is None
                or hard_check.status != "accepted"
                or sandbox is None
                or sandbox.status != "passed"
                or critic_status != "succeeded"
                or critic_blocks
            )
            if non_retryable_provider_error:
                route, stop_reason, final_status = "fail", "non_retryable_provider_error", "failed"
            elif browser_infrastructure_error:
                route, stop_reason, final_status = "fail", "browser_infrastructure_error", "failed"
            elif needs_revision and attempt < max_attempts:
                route, stop_reason, final_status = "revise", None, None
            elif needs_revision:
                route, stop_reason, final_status = "block", "max_attempts", "blocked"
            else:
                warning_only = any(issue.severity == "warning" for issue in feedback)
                route, stop_reason, final_status = "accept", "at_risk" if warning_only else "accepted", "at_risk" if warning_only else "accepted"

        if candidate is not None:
            candidate.issues = _dedupe_issues([*candidate.issues, *feedback])
            if route == "accept":
                candidate.status = "accepted"
            elif final_status in {"blocked", "failed"}:
                candidate.status = "blocked"
        elif final_status in {"blocked", "failed"} and not feedback:
            feedback.append(_issue(
                task=task,
                suffix=f"generation-error-{attempt}",
                category="coverage",
                severity="blocking",
                message=state.get("generation_error") or state.get("critic_error") or "组件 loop 未生成可验证规格。",
                suggested_action="检查模型调用、规格结构和当前知识单元的来源绑定。",
            ))

        trace_attempt = InteractiveComponentAttemptTrace(
            attempt=attempt,
            generation_stage=state.get("generation_stage", "initial"),
            generation_prompt=state.get("generation_prompt", ""),
            revision_prompt=state.get("revision_prompt"),
            generation_raw_output=state.get("generation_raw_output", ""),
            generation_parsed_output=state.get("generation_parsed_output"),
            candidate_artifact=candidate,
            generation_status=generation_status,
            generation_error=state.get("generation_error"),
            generation_error_category=state.get("generation_error_category"),
            generation_provider_metadata=dict(state.get("generation_provider_metadata", {})),
            hard_check=hard_check,
            sandbox_report=sandbox,
            critic_prompt=state.get("critic_prompt"),
            critic_raw_output=state.get("critic_raw_output", ""),
            critic_parsed_output=state.get("critic_parsed_output"),
            critic_status=critic_status,
            critic_error=state.get("critic_error"),
            critic_error_category=state.get("critic_error_category"),
            critic_provider_metadata=dict(state.get("critic_provider_metadata", {})),
            feedback=_dedupe_issues(feedback),
            route=route,
            stop_reason=stop_reason,
        )
        attempts = [*state.get("attempts", []), trace_attempt]
        result: dict[str, Any] = {"attempts": attempts, "route": route, "final_artifact": candidate}
        if final_status is not None:
            result["interactive_component_loop_trace"] = InteractiveComponentLoopTrace(
                trace_id=f"interactive-trace-{uuid.uuid4().hex[:12]}",
                run_id=state["run_id"],
                plan=state["plan"],
                task_id=task.task_id,
                knowledge_unit_id=task.knowledge_unit_id,
                context_pack_id=task.context_pack_id,
                max_attempts=max_attempts,
                attempts=attempts,
                final_status=final_status,
                final_attempt=attempt,
                stop_reason=stop_reason or "max_attempts",
                final_component_artifact_id=candidate.artifact_id if candidate is not None else None,
            )
        return result

    def next_route(state: InteractiveComponentState) -> str:
        return str(state.get("route", "fail"))

    graph = StateGraph(InteractiveComponentState)
    graph.add_node("generate_candidate", generate_candidate)
    graph.add_node("hard_check_candidate", hard_check_candidate)
    graph.add_node("sandbox_candidate", sandbox_candidate)
    graph.add_node("critic_candidate", critic_candidate)
    graph.add_node("route_candidate", route_candidate)
    graph.add_edge(START, "generate_candidate")
    graph.add_edge("generate_candidate", "hard_check_candidate")
    graph.add_conditional_edges(
        "hard_check_candidate",
        after_hard_check,
        {"sandbox": "sandbox_candidate", "route": "route_candidate"},
    )
    graph.add_conditional_edges(
        "sandbox_candidate",
        after_sandbox,
        {"critic": "critic_candidate", "route": "route_candidate"},
    )
    graph.add_edge("critic_candidate", "route_candidate")
    graph.add_conditional_edges(
        "route_candidate",
        next_route,
        {"accept": END, "revise": "generate_candidate", "block": END, "fail": END, "not_needed": END},
    )
    return graph.compile()


def _not_needed_result(plan: InteractiveComponentPlan, *, max_attempts: int) -> dict[str, Any]:
    trace = InteractiveComponentLoopTrace(
        trace_id=f"interactive-trace-{uuid.uuid4().hex[:12]}",
        run_id=plan.run_id,
        plan=plan,
        max_attempts=max_attempts,
        attempts=[InteractiveComponentAttemptTrace(
            attempt=0,
            generation_stage="skipped",
            generation_status="skipped",
            route="not_needed",
            stop_reason="not_needed",
        )],
        final_status="not_needed",
        final_attempt=0,
        stop_reason="not_needed",
    )
    return {
        "interactive_component_plan": plan,
        "interactive_component_artifacts": [],
        "interactive_component_loop_trace": trace,
        "interactive_component_status": "not_needed",
        "interactive_component_summary": {
            "component_count": 0,
            "attempt_count": 0,
            "final_status": "not_needed",
            "stop_reason": "not_needed",
        },
    }


def run_interactive_component_loop(
    provider: ModelProvider,
    *,
    run_id: str,
    tasks: list[ContentTask],
    context_packs: list[ContextPack],
    content_artifacts: list[ContentArtifact],
    units: list[KnowledgeUnit],
    valid_source_refs: list[str],
    max_attempts: int,
    sandbox_runner: SandboxRunner = run_interactive_component_sandbox,
) -> dict[str, Any]:
    """Plan and execute at most one sandbox-validated A-004 component."""

    plan, task, unit, content, pack = plan_interactive_component(
        run_id=run_id,
        tasks=tasks,
        context_packs=context_packs,
        content_artifacts=content_artifacts,
        units=units,
    )
    if task is None or unit is None or content is None or pack is None:
        return _not_needed_result(plan, max_attempts=max_attempts)
    task.status = "generating"
    state = build_interactive_component_subgraph(
        provider,
        max_attempts=max_attempts,
        sandbox_runner=sandbox_runner,
    ).invoke({
        "run_id": run_id,
        "plan": plan,
        "task": task,
        "unit": unit,
        "content_artifact": content,
        "context_pack": pack,
        "valid_source_refs": valid_source_refs,
        "max_attempts": max_attempts,
        "attempts": [],
    })
    trace: InteractiveComponentLoopTrace = state["interactive_component_loop_trace"]
    final_artifact = state.get("final_artifact")
    if trace.final_status in {"accepted", "at_risk"}:
        task.status = "accepted"
    elif trace.final_status == "not_needed":
        task.status = "not_needed"
    else:
        task.status = "blocked"
    artifacts = [final_artifact] if final_artifact is not None else []
    return {
        "interactive_component_plan": trace.plan,
        "interactive_component_task": task,
        "interactive_component_artifacts": artifacts,
        "interactive_component_loop_trace": trace,
        "interactive_component_status": trace.final_status,
        "interactive_component_summary": {
            "component_count": len([artifact for artifact in artifacts if artifact.status == "accepted"]),
            "attempt_count": len(trace.attempts),
            "final_status": trace.final_status,
            "stop_reason": trace.stop_reason,
        },
    }


__all__ = [
    "build_interactive_component_subgraph",
    "interactive_component_hard_check",
    "plan_interactive_component",
    "run_interactive_component_loop",
]
