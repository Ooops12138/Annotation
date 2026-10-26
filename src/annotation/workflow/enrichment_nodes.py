"""Workflow stage nodes with explicit configuration and returned state updates."""

from __future__ import annotations

from typing import Any
from annotation.config import (
    STORAGE_DIR,
    interactive_component_max_attempts as configured_interactive_component_max_attempts,
)
from annotation.workflow.content import write_content_run_artifact
from annotation.workflow.fact_check import run_fact_check_loop
from annotation.workflow.interactive_components import run_interactive_component_loop
from annotation.workflow.models import WorkflowState
from annotation.workflow.quiz import validate_quiz_coverage
from annotation.workflow.context import WorkflowContext
import logging

logger = logging.getLogger(__name__)


def fact_check_artifacts(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    """Apply A-003 after accepted explanations and quiz structure checks."""

    blueprint = state.get("content_blueprint", state["blueprint"])
    result = run_fact_check_loop(
        ctx.provider,
        run_id=state["run_id"],
        blueprint=blueprint,
        tasks=state.get("content_tasks", []),
        context_packs=state.get("context_packs", []),
        content_artifacts=state.get("content_artifacts", []),
        quiz_artifacts=state.get("quiz_artifacts", []),
        source_blocks=state.get("source_blocks", []),
        valid_source_refs=state.get("source_refs", []),
        policy=ctx.fact_check_policy,
        web_search_skill=ctx.run_resources.web(ctx.run_control),
        additional_retrieval_enabled=ctx.additional_fact_retrieval,
        max_workers=ctx.workers,
    )
    final_quiz_artifacts = result["quiz_artifacts"]
    quiz_coverage = validate_quiz_coverage(
        final_quiz_artifacts,
        blueprint,
        context_packs={
            pack.context_pack_id: pack for pack in state.get("context_packs", [])
        },
        valid_source_refs=state.get("source_refs", []),
    )
    artifact_path = write_content_run_artifact(
        run_id=state["run_id"],
        blueprint_version=f"{blueprint.artifact_id}:v{blueprint.version}",
        tasks=state.get("content_tasks", []),
        context_packs=state.get("context_packs", []),
        artifacts=result["content_artifacts"],
        quiz_artifacts=final_quiz_artifacts,
        quiz_coverage=quiz_coverage,
        checks=state.get("content_artifact_checks", {}),
        provider_metadata=state.get("provider_metadata", {}),
        content_task_planning_metadata=state.get("content_task_planning_metadata"),
        content_loop_traces=state.get("content_loop_traces", []),
        content_loop_summary=dict(state.get("content_loop_summary", {})),
        root=STORAGE_DIR / "artifacts" / "content",
    )
    result.update(
        {
            "fact_check_status": result["fact_check_artifact"].status,
            "quiz_coverage_report": quiz_coverage,
            "content_artifact_path": str(artifact_path.resolve()),
            "content_loop_trace_path": str(artifact_path.resolve()),
        }
    )
    return result


def interactive_component_loop(
    ctx: WorkflowContext, state: WorkflowState
) -> dict[str, Any]:
    """Run A-004 only after A-003 has finalized content and quiz artifacts."""

    kwargs: dict[str, Any] = {}
    if ctx.sandbox_runner is not None:
        kwargs["sandbox_runner"] = ctx.sandbox_runner
    try:
        component_max_attempts = configured_interactive_component_max_attempts(
            ctx.component_max_attempts
        )
        return run_interactive_component_loop(
            ctx.provider,
            run_id=state["run_id"],
            tasks=state.get("content_tasks", []),
            context_packs=state.get("context_packs", []),
            content_artifacts=state.get("content_artifacts", []),
            units=list(
                state.get("content_blueprint", state["blueprint"]).knowledge_units
            ),
            valid_source_refs=state.get("source_refs", []),
            max_attempts=component_max_attempts,
            max_workers=ctx.workers,
            **kwargs,
        )
    except Exception as exc:
        logger.exception("Unexpected interactive component stage failure")
        # Components are optional output. Preserve diagnostics without
        # putting failed candidates or their errors in the learner document.
        return {
            "interactive_component_artifacts": [],
            "interactive_component_status": "failed",
            "interactive_component_summary": {
                "component_count": 0,
                "final_status": "failed",
                "stop_reason": "unexpected_error",
                "error": f"{type(exc).__name__}: {exc}",
            },
        }
