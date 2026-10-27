"""Workflow stage nodes with explicit configuration and returned state updates."""

from __future__ import annotations

import uuid
from typing import Any, Literal
from annotation.config import STORAGE_DIR
from annotation.domain.artifacts import (
    ContentArtifact,
    ContentAttemptTrace,
    ContentHardCheckResult,
    ContentTask,
    ContentUnitLoopTrace,
    ContextPack,
    KnowledgeUnit,
    LearningBlueprint,
    QuizArtifact,
)
from annotation.workflow.content import write_content_run_artifact
from annotation.ingestion.semantic_chunker import build_semantic_blocks
from annotation.workflow.concurrency import ordered_batches
from annotation.workflow.content_reflection import build_content_reflection_subgraph
from annotation.workflow.course_architect import (
    plan_content_tasks_with_architect,
    teaching_blueprint,
)
from annotation.workflow.content_support import (
    blocked_content_artifact,
    content_issue,
    content_loop_summary,
    merge_messages,
)
from annotation.workflow.models import WorkflowState
from annotation.workflow.quiz import (
    blocked_quiz_artifact,
    generate_quiz_artifact,
    retrieve_quiz_context,
    validate_quiz_coverage,
)
from annotation.workflow.context import WorkflowContext
from dataclasses import dataclass
from functools import partial
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContentGenerationContext:
    ctx: WorkflowContext
    state: WorkflowState
    blueprint: LearningBlueprint
    units: dict[str, KnowledgeUnit]
    packs: dict[str, ContextPack]
    subgraph: Any


def content_preflight_trace(
    ctx: WorkflowContext,
    *,
    run_id: str,
    task: ContentTask,
    unit: KnowledgeUnit | None,
    pack: ContextPack | None,
    reason: Literal[
        "context_pack_missing",
        "context_pack_empty",
        "context_pack_source_over_budget",
        "upstream_failure",
    ],
    message: str,
    final_status: Literal["blocked", "skipped"],
) -> tuple[ContentUnitLoopTrace, list[ContentArtifact]]:
    category: Literal["coverage", "source"] = (
        "source" if reason == "context_pack_source_over_budget" else "coverage"
    )
    issue = content_issue(
        task=task,
        suffix=reason,
        category=category,
        severity="blocking",
        message=message,
        target_id=unit.artifact_id if unit is not None else task.knowledge_unit_id,
        source_refs=list(pack.omitted_source_refs)
        if pack is not None and reason == "context_pack_source_over_budget"
        else [],
    )
    candidate_artifact: ContentArtifact | None = None
    if unit is not None:
        candidate_artifact = blocked_content_artifact(
            task=task,
            unit=unit,
            context_pack=pack,
            run_id=run_id,
            provider_name=getattr(ctx.provider, "provider", "unknown"),
            attempt=0,
            error=message,
            issues=[issue],
        )
    check = ContentHardCheckResult(
        status="blocked",
        issues=[issue],
        checked_artifact_id=candidate_artifact.artifact_id
        if candidate_artifact is not None
        else None,
    )
    trace_attempt = ContentAttemptTrace(
        attempt=0,
        generation_stage="skipped",
        generation_status="skipped",
        candidate_artifact=candidate_artifact,
        hard_check=check,
        critic_status="skipped",
        feedback=[issue],
        route="block" if final_status == "blocked" else "fail",
        stop_reason=reason,
    )
    trace = ContentUnitLoopTrace(
        trace_id=f"content-trace-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        task_id=task.task_id,
        knowledge_unit_id=unit.artifact_id
        if unit is not None
        else task.knowledge_unit_id,
        context_pack_id=pack.context_pack_id if pack is not None else None,
        max_attempts=ctx.content_max_attempts,
        attempts=[trace_attempt],
        final_status=final_status,
        final_attempt=0,
        stop_reason=reason,
        final_content_artifact_id=candidate_artifact.artifact_id
        if candidate_artifact is not None
        else None,
    )
    return trace, [candidate_artifact] if candidate_artifact is not None else []


def generate_content_task(
    batch: ContentGenerationContext, task: ContentTask
) -> dict[str, Any]:
    task = task.model_copy(deep=True)
    ctx, state = batch.ctx, batch.state
    units, packs, blueprint = batch.units, batch.packs, batch.blueprint
    subgraph = batch.subgraph
    semantic_blocks = state["semantic_blocks"]
    if ctx.run_control:
        ctx.run_control.raise_if_stopped()
    artifacts, traces, updated_packs, warnings = [], [], [], []
    checks = {}
    provider_metadata = {}
    unit = units.get(task.knowledge_unit_id)
    pack = packs[task.task_id].model_copy(deep=True) if task.task_id in packs else None
    task.status = "generating"
    if unit is None or pack is None:
        trace, final_artifacts = content_preflight_trace(
            ctx,
            run_id=state["run_id"],
            task=task,
            unit=unit,
            pack=pack,
            reason="context_pack_missing",
            message="当前知识单元缺少 ContextPack，未调用模型。",
            final_status="blocked",
        )
        task.status = "blocked"
        checks[task.task_id] = "blocked"
        warnings.append(f"content_context_pack_missing:{task.task_id}")
        traces.append(trace)
        artifacts.extend(final_artifacts)
        return {
            "task": task,
            "artifacts": artifacts,
            "traces": traces,
            "updated_packs": updated_packs,
            "checks": checks,
            "warnings": warnings,
            "provider_metadata": provider_metadata,
        }
    if pack.omitted_source_refs:
        trace, final_artifacts = content_preflight_trace(
            ctx,
            run_id=state["run_id"],
            task=task,
            unit=unit,
            pack=pack,
            reason="context_pack_source_over_budget",
            message=f"ContextPack 缺少必需来源：{', '.join(pack.omitted_source_refs)}，未调用模型。",
            final_status="blocked",
        )
        task.status = "blocked"
        checks[task.task_id] = "blocked"
        warnings.append(
            f"content_context_pack_source_over_budget:{task.task_id}:{','.join(pack.omitted_source_refs)}"
        )
        traces.append(trace)
        artifacts.extend(final_artifacts)
        return {
            "task": task,
            "artifacts": artifacts,
            "traces": traces,
            "updated_packs": updated_packs,
            "checks": checks,
            "warnings": warnings,
            "provider_metadata": provider_metadata,
        }

    result = subgraph.invoke(
        {
            "run_id": state["run_id"],
            "task": task,
            "unit": unit,
            "context_pack": pack,
            "valid_source_refs": list(
                dict.fromkeys([*state.get("source_refs", []), *pack.source_refs])
            ),
            "max_attempts": ctx.content_max_attempts,
            "attempts": [],
            "source_blocks": state.get("source_blocks", []),
            "semantic_blocks": semantic_blocks,
            "blueprint": blueprint,
        },
        durability="async",
    )
    trace = result["content_loop_trace"]
    generated_pack = result.get("context_pack")
    if generated_pack is not None:
        updated_packs = [pack for pack in updated_packs if pack.task_id != task.task_id]
        updated_packs.append(generated_pack)
    final_artifacts = list(result.get("final_artifacts", []))
    traces.append(trace)
    artifacts.extend(final_artifacts)
    if trace.final_status == "accepted":
        task.status = "accepted"
        checks[task.task_id] = "accepted"
    else:
        task.status = "blocked"
        checks[task.task_id] = "failed" if trace.final_status == "failed" else "blocked"
        warnings.append(
            f"content_reflection_{trace.final_status}:{task.task_id}:{trace.stop_reason}"
        )
    if trace.attempts:
        last_attempt = trace.attempts[-1]
        metadata = (
            last_attempt.generation_provider_metadata
            or last_attempt.critic_provider_metadata
        )
        if metadata:
            provider_metadata = dict(metadata)

    return {
        "task": task,
        "artifacts": artifacts,
        "traces": traces,
        "updated_packs": updated_packs,
        "checks": checks,
        "warnings": warnings,
        "provider_metadata": provider_metadata,
    }


def plan_content_tasks(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    result = plan_content_tasks_with_architect(
        ctx.provider,
        run_id=state["run_id"],
        blueprint=state["blueprint"],
    )
    metadata = result.get("content_task_planning_metadata", {})
    status = str(metadata.get("status", "unknown"))
    planning_errors = []
    if status != "succeeded":
        planning_errors.append(f"course_architect_{status}")
    return {
        "content_blueprint": teaching_blueprint(
            state["blueprint"], result["content_tasks"]
        )
        if status == "succeeded"
        else state["blueprint"],
        "content_tasks": result["content_tasks"],
        "content_task_planning_metadata": metadata,
        "warnings": merge_messages(state.get("warnings"), result.get("warnings", [])),
        "errors": merge_messages(state.get("errors"), planning_errors),
        "workflow_status": "blocked"
        if status != "succeeded"
        else state.get("workflow_status", ""),
    }


def record_content_task_planning_blocked_run(
    ctx: WorkflowContext, state: WorkflowState
) -> dict[str, Any]:
    return {"workflow_status": "blocked"}


def prepare_content_tools(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    capabilities = getattr(ctx.provider, "capabilities", None)
    context_window = getattr(capabilities, "context_window", None)
    packs: list[ContextPack] = []
    warnings: list[str] = []
    # Content uses semantic chunks; fact checking continues to use original
    # source blocks in its own stage so its citation granularity is unchanged.
    semantic_blocks = build_semantic_blocks(state["source_blocks"])
    ctx.run_resources.prepare_index(semantic_blocks)
    # Prepare tools only; the agent acquires its own evidence.
    for task in state.get("content_tasks", []):
        packs.append(
            ContextPack(
                context_pack_id=f"ctx-{task.task_id}",
                run_id=state["run_id"],
                task_id=task.task_id,
                knowledge_unit_id=task.knowledge_unit_id,
                estimated_input_tokens=0,
                input_budget_tokens=context_window or 12000,
            )
        )
    return {
        "semantic_blocks": semantic_blocks,
        "context_packs": packs,
        "warnings": merge_messages(state.get("warnings"), warnings),
    }


def content_reflection_loops(
    ctx: WorkflowContext, state: WorkflowState
) -> dict[str, Any]:
    """Run isolated task subgraphs in bounded batches; merge in task order."""

    blueprint = state.get("content_blueprint", state["blueprint"])
    units = {unit.artifact_id: unit for unit in blueprint.knowledge_units}
    packs = {pack.task_id: pack for pack in state.get("context_packs", [])}
    artifacts: list[ContentArtifact] = []
    traces: list[ContentUnitLoopTrace] = []
    checks: dict[str, str] = {}
    warnings: list[str] = []
    provider_metadata: dict[str, Any] = dict(state.get("provider_metadata", {}))
    semantic_blocks = state["semantic_blocks"]
    retrieval_service = ctx.run_resources.retrieval(semantic_blocks, ctx.run_control)
    subgraph = build_content_reflection_subgraph(
        ctx.provider,
        max_attempts=ctx.content_max_attempts,
        retrieval_service=retrieval_service,
    )
    warnings.extend(ctx.run_resources.warnings)

    tasks = [task.model_copy(deep=True) for task in state.get("content_tasks", [])]
    batch = ContentGenerationContext(ctx, state, blueprint, units, packs, subgraph)
    results = ordered_batches(
        partial(generate_content_task, batch),
        tasks,
        workers=ctx.workers,
        stop=lambda result: any(
            trace.final_status == "failed" for trace in result["traces"]
        ),
    )
    for index, result in enumerate(results):
        tasks[index] = result["task"]
        artifacts.extend(result["artifacts"])
        traces.extend(result["traces"])
        checks.update(result["checks"])
        warnings.extend(result["warnings"])
        for generated_pack in result["updated_packs"]:
            packs[generated_pack.task_id] = generated_pack
        if result["provider_metadata"]:
            provider_metadata = result["provider_metadata"]
    for task in tasks[len(results) :]:
        task.status = "blocked"
        trace, skipped_artifacts = content_preflight_trace(
            ctx,
            run_id=state["run_id"],
            task=task,
            unit=units.get(task.knowledge_unit_id),
            pack=packs.get(task.task_id),
            reason="upstream_failure",
            message="先前批次发生技术失败，当前任务未执行。",
            final_status="skipped",
        )
        traces.append(trace)
        artifacts.extend(skipped_artifacts)
        checks[task.task_id] = "failed"
    updated_packs = [packs[task.task_id] for task in tasks if task.task_id in packs]

    summary = content_loop_summary(traces)
    status = summary["final_status"]
    result: dict[str, Any] = {
        "content_tasks": tasks,
        "context_packs": updated_packs,
        "content_artifacts": artifacts,
        "content_loop_traces": traces,
        "content_loop_summary": summary,
        "content_loop_status": status,
        "content_artifact_checks": checks,
        "provider_metadata": provider_metadata,
        "warnings": merge_messages(state.get("warnings"), warnings),
    }
    # A blocked/failed content loop must be independently reconstructible;
    # successful runs write the same v2 content snapshot after quiz output
    # is available below.
    if status != "accepted":
        artifact_path = write_content_run_artifact(
            run_id=state["run_id"],
            blueprint_version=f"{blueprint.artifact_id}:v{blueprint.version}",
            tasks=tasks,
            context_packs=updated_packs,
            artifacts=artifacts,
            quiz_artifacts=[],
            quiz_coverage=None,
            checks=checks,
            provider_metadata=provider_metadata,
            content_task_planning_metadata=state.get("content_task_planning_metadata"),
            content_loop_traces=traces,
            content_loop_summary=summary,
            root=STORAGE_DIR / "artifacts" / "content",
        )
        result["content_artifact_path"] = str(artifact_path.resolve())
        result["content_loop_trace_path"] = str(artifact_path.resolve())
    return result


def record_content_blocked_run(
    ctx: WorkflowContext, state: WorkflowState
) -> dict[str, Any]:
    return {"workflow_status": "blocked", "content_loop_status": "blocked"}


def record_content_failed_run(
    ctx: WorkflowContext, state: WorkflowState
) -> dict[str, Any]:
    failed = [
        trace.task_id
        for trace in state.get("content_loop_traces", [])
        if trace.final_status == "failed"
    ]
    message = f"content_reflection_failed: {', '.join(failed) or 'unknown'}"
    return {
        "workflow_status": "failed",
        "content_loop_status": "failed",
        "errors": merge_messages(state.get("errors"), [message]),
    }


@dataclass(frozen=True)
class QuizTaskResult:
    artifact: QuizArtifact | None
    context_pack: ContextPack | None


def generate_task_quiz(
    ctx: WorkflowContext,
    state: WorkflowState,
    task: ContentTask,
    *,
    units: dict[str, KnowledgeUnit],
    packs: dict[str, ContextPack],
    retrieval_service: Any,
) -> QuizTaskResult:
    unit = units.get(task.knowledge_unit_id)
    if unit is None:
        return QuizTaskResult(None, None)  # Coverage records the missing unit.
    pack = packs.get(task.task_id)
    if pack is None:
        pack = ContextPack(
            context_pack_id=f"ctx-{task.task_id}",
            run_id=task.run_id,
            task_id=task.task_id,
            knowledge_unit_id=unit.artifact_id,
            estimated_input_tokens=0,
            input_budget_tokens=0,
        )
        return QuizTaskResult(
            blocked_quiz_artifact(
                task=task,
                unit=unit,
                context_pack=pack,
                error="ContextPack missing for quiz generation",
            ),
            pack,
        )
    pack = pack.model_copy(deep=True)
    if task.quiz_count != 0:
        try:
            pack = retrieve_quiz_context(
                ctx.provider,
                task=task,
                unit=unit,
                context_pack=pack,
                retrieval_service=retrieval_service,
                semantic_blocks=state.get("semantic_blocks", []),
            )
        except Exception:
            logger.exception("Quiz retrieval planning failed for %s", task.task_id)
    try:
        artifact = generate_quiz_artifact(
            ctx.provider,
            task=task,
            unit=unit,
            context_pack=pack,
            run_id=state["run_id"],
            retrieval_service=None,
            accepted_content=next((content for content in state.get("content_artifacts", [])
                if content.task_id == task.task_id and content.status == "accepted"), None),
        )
    except Exception as exc:
        logger.exception("Unexpected quiz generation failure for %s", task.task_id)
        artifact = blocked_quiz_artifact(
            task=task,
            unit=unit,
            context_pack=pack,
            error=f"{type(exc).__name__}: {exc}",
            provider_name=ctx.provider.provider,
        )
    return QuizTaskResult(artifact, pack)


def generate_quiz_artifacts(
    ctx: WorkflowContext, state: WorkflowState
) -> dict[str, Any]:
    """Optional quizzes retain strict diagnostics without changing body acceptance."""
    blueprint = state.get("content_blueprint", state["blueprint"])
    units = {unit.artifact_id: unit for unit in blueprint.knowledge_units}
    packs = {pack.task_id: pack for pack in state.get("context_packs", [])}
    tasks = state.get("content_tasks", [])
    retrieval_service = ctx.run_resources.retrieval(
        state["semantic_blocks"], ctx.run_control
    )
    results = ordered_batches(
        partial(
            generate_task_quiz,
            ctx,
            state,
            units=units,
            packs=packs,
            retrieval_service=retrieval_service,
        ),
        tasks,
        workers=ctx.workers,
    )
    quizzes = [result.artifact for result in results if result.artifact is not None]
    for result in results:
        if result.context_pack is not None:
            packs[result.context_pack.task_id] = result.context_pack
    updated_packs = list(packs.values())
    coverage = validate_quiz_coverage(
        quizzes,
        blueprint,
        context_packs={pack.context_pack_id: pack for pack in packs.values()},
        valid_source_refs=state.get("source_refs", []),
    )
    path = write_content_run_artifact(
        run_id=state["run_id"],
        blueprint_version=f"{blueprint.artifact_id}:v{blueprint.version}",
        tasks=tasks,
        context_packs=updated_packs,
        artifacts=state.get("content_artifacts", []),
        quiz_artifacts=quizzes,
        quiz_coverage=coverage,
        checks=state.get("content_artifact_checks", {}),
        provider_metadata=state.get("provider_metadata", {}),
        content_task_planning_metadata=state.get("content_task_planning_metadata"),
        content_loop_traces=state.get("content_loop_traces", []),
        content_loop_summary=dict(state.get("content_loop_summary", {})),
        root=STORAGE_DIR / "artifacts" / "content",
    )
    return {
        "context_packs": updated_packs,
        "quiz_artifacts": quizzes,
        "quiz_coverage_report": coverage,
        "content_artifact_path": str(path.resolve()),
        "content_loop_trace_path": str(path.resolve()),
    }
