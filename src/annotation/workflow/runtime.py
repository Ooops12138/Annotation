"""Compile the workflow and execute it with native LangGraph checkpoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any
import uuid

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command, interrupt

from annotation.config import (
    blueprint_max_attempts as configured_blueprint_max_attempts,
    content_reflection_max_attempts as configured_content_reflection_max_attempts,
    fact_check_max_claims_per_unit as configured_fact_check_max_claims_per_unit,
    fact_check_max_corrections as configured_fact_check_max_corrections,
    fact_check_additional_retrieval_enabled as configured_additional_retrieval,
    fact_check_web_enabled as configured_fact_check_web_enabled,
    fact_check_web_query_limit,
    interactive_component_max_attempts as configured_component_max_attempts,
)
from annotation.domain.artifacts import FactCheckPolicy, LearningBlueprint
from annotation.providers import ModelProvider, create_provider_from_env
from annotation.workflow import (
    blueprint_nodes,
    content_nodes,
    enrichment_nodes,
    review_nodes,
)
from annotation.workflow.checkpoints import workflow_checkpointer
from annotation.workflow.concurrency import workflow_max_workers
from annotation.workflow.context import RunResources, WorkflowContext
from annotation.workflow.control import ControlledPort, RunControl, RunStopped
from annotation.workflow.models import WorkflowState
from annotation.prompt_loader import prompt_profile_scope, validate_prompt_profile
from annotation.workflow.persistence import persist_workflow_snapshots


NODES = {
    name: module
    for module, names in (
        (
            blueprint_nodes,
            (
                "ingest",
                "prepare_blueprint",
                "hierarchical_blueprint",
                "generate_blueprint_attempt",
                "check_blueprint_attempt",
                "route_blueprint",
                "record_blocked_run",
                "record_failed_run",
            ),
        ),
        (
            content_nodes,
            (
                "plan_content_tasks",
                "record_content_task_planning_blocked_run",
                "prepare_content_tools",
                "content_reflection_loops",
                "record_content_blocked_run",
                "record_content_failed_run",
                "generate_quiz_artifacts",
            ),
        ),
        (enrichment_nodes, ("fact_check_artifacts", "interactive_component_loop")),
        (
            review_nodes,
            ("assemble_document_ir", "validate_document_ir", "review", "assemble"),
        ),
    )
    for name in names
}


def next_blueprint_route(state: WorkflowState) -> str:
    return state.get("blueprint_route", "fail")


def next_content_route(state: WorkflowState) -> str:
    return {"accepted": "accept", "blocked": "block"}.get(
        state.get("content_loop_status"), "fail"
    )


def next_planning_route(state: WorkflowState) -> str:
    return (
        "accept"
        if state.get("content_task_planning_metadata", {}).get("status") == "succeeded"
        else "block"
    )


def blueprint_mode(state: WorkflowState) -> str:
    return state.get("blueprint_mode", "single")


def document_route(state: WorkflowState) -> str:
    return "block" if state.get("workflow_status") == "blocked" else "accept"


# Scheduling lives here only. Checkpoints persist LangGraph's own next tasks.
EDGES = {
    "ingest": "prepare_blueprint",
    "generate_blueprint_attempt": "check_blueprint_attempt",
    "check_blueprint_attempt": "route_blueprint",
    "record_blocked_run": END,
    "record_failed_run": END,
    "record_content_task_planning_blocked_run": END,
    "prepare_content_tools": "content_reflection_loops",
    "record_content_blocked_run": END,
    "record_content_failed_run": END,
    "generate_quiz_artifacts": "fact_check_artifacts",
    "fact_check_artifacts": "interactive_component_loop",
    "interactive_component_loop": "assemble_document_ir",
    "review": "assemble",
    "assemble": END,
}
CONDITIONAL_EDGES = {
    "prepare_blueprint": (
        blueprint_mode,
        {
            "single": "generate_blueprint_attempt",
            "fixture": "generate_blueprint_attempt",
            "hierarchical": "hierarchical_blueprint",
        },
    ),
    "hierarchical_blueprint": (
        next_blueprint_route,
        {
            "accept": "plan_content_tasks",
            "block": "record_blocked_run",
            "fail": "record_failed_run",
        },
    ),
    "route_blueprint": (
        next_blueprint_route,
        {
            "accept": "plan_content_tasks",
            "revise": "generate_blueprint_attempt",
            "block": "record_blocked_run",
            "fail": "record_failed_run",
        },
    ),
    "plan_content_tasks": (
        next_planning_route,
        {
            "accept": "prepare_content_tools",
            "block": "record_content_task_planning_blocked_run",
        },
    ),
    "content_reflection_loops": (
        next_content_route,
        {
            "accept": "generate_quiz_artifacts",
            "block": "record_content_blocked_run",
            "fail": "record_content_failed_run",
        },
    ),
    "assemble_document_ir": (
        document_route,
        {"accept": "validate_document_ir", "block": "record_blocked_run"},
    ),
    "validate_document_ir": (
        document_route,
        {"accept": "review", "block": "record_blocked_run"},
    ),
}


def tracked_node(
    ctx: WorkflowContext,
    name: str,
    node: Callable[..., Any],
    progress_callback: Callable[..., Any] | None,
    state: WorkflowState,
    runtime: Runtime[RunResources],
) -> dict[str, Any]:
    if ctx.run_control and ctx.run_control.stop_requested:
        interrupt({"reason": "user_requested_stop", "stage": name})
        ctx.run_control.raise_if_stopped()
    if progress_callback:
        progress_callback(name, state, "started")
    try:
        with prompt_profile_scope(state.get("prompt_profile")):
            update = _invoke_node(ctx, node, state, runtime)
    except RunStopped:
        interrupt({"reason": "user_requested_stop", "stage": name})
        raise
    if progress_callback:
        progress_callback(name, {**state, **update}, "completed")
    return update


def _invoke_node(ctx: WorkflowContext, node: Callable[..., Any], state: WorkflowState,
                 runtime: Runtime[RunResources]) -> dict[str, Any]:
    if runtime.context is None:
        # Direct compiled-graph callers may opt out of cross-node client reuse.
        with RunResources() as resources:
            return node(replace(ctx, resources=resources), state)
    return node(replace(ctx, resources=runtime.context), state)


def build_minimal_graph(
    provider: ModelProvider | None = None,
    *,
    progress_callback: Callable[[str, WorkflowState, str], None] | None = None,
    run_control: RunControl | None = None,
    blueprint_max_attempts: int | None = None,
    content_reflection_max_attempts: int | None = None,
    fact_check_max_corrections: int | None = None,
    fact_check_max_claims_per_unit: int | None = None,
    fact_check_web_enabled: bool | None = None,
    interactive_component_max_attempts: int | None = None,
    interactive_component_sandbox_runner: Callable[..., Any] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    model_provider = provider if provider is not None else create_provider_from_env()
    ctx = WorkflowContext(
        provider=ControlledPort(model_provider, run_control)
        if run_control
        else model_provider,
        blueprint_max_attempts=configured_blueprint_max_attempts(
            blueprint_max_attempts
        ),
        content_max_attempts=configured_content_reflection_max_attempts(
            content_reflection_max_attempts
        ),
        workers=workflow_max_workers(),
        fact_check_policy=FactCheckPolicy(
            max_claims_per_unit=configured_fact_check_max_claims_per_unit(
                fact_check_max_claims_per_unit
            ),
            max_corrections_per_unit=configured_fact_check_max_corrections(
                fact_check_max_corrections
            ),
            web_enabled=configured_fact_check_web_enabled(fact_check_web_enabled),
            web_query_limit=fact_check_web_query_limit(),
        ),
        additional_fact_retrieval=configured_additional_retrieval(),
        component_max_attempts=configured_component_max_attempts(
            interactive_component_max_attempts
        ),
        sandbox_runner=interactive_component_sandbox_runner,
        run_control=run_control,
    )
    graph = StateGraph(WorkflowState, context_schema=RunResources)
    for name, module in NODES.items():
        function = getattr(module, name)
        graph.add_node(
            name, partial(tracked_node, ctx, name, function, progress_callback)
        )
    graph.add_edge(START, "ingest")
    for source, target in EDGES.items():
        graph.add_edge(source, target)
    for source, (route, targets) in CONDITIONAL_EDGES.items():
        graph.add_conditional_edges(source, route, targets)
    return graph.compile(checkpointer=checkpointer)


def run_minimal_workflow(
    *,
    provider: ModelProvider | None = None,
    run_id: str = "run-demo-001",
    blueprint: LearningBlueprint | None = None,
    pdf_path: str | Path | None = None,
    document_id: str | None = None,
    blueprint_max_attempts: int | None = None,
    content_reflection_max_attempts: int | None = None,
    fact_check_max_corrections: int | None = None,
    fact_check_max_claims_per_unit: int | None = None,
    fact_check_web_enabled: bool | None = None,
    interactive_component_max_attempts: int | None = None,
    interactive_component_sandbox_runner: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, WorkflowState, str], None] | None = None,
    run_control: RunControl | None = None,
    resume_checkpoint: dict[str, Any] | None = None,
    prompt_profile: str | None = None,
) -> WorkflowState:
    if resume_checkpoint and resume_checkpoint.get("run_id") != run_id:
        raise ValueError("native resume must keep the original run_id")
    initial: WorkflowState = {
        "run_id": run_id,
        "prompt_profile": validate_prompt_profile(prompt_profile),
        "blueprint_attempts": [],
        "blueprint_trace_id": f"trace-{uuid.uuid4().hex[:12]}",
    }
    if blueprint:
        initial.update(blueprint=blueprint, blueprint_preloaded=True)
    if pdf_path:
        initial["pdf_path"] = str(pdf_path)
    if document_id:
        initial["document_id"] = document_id
    config = {"configurable": {"thread_id": f"{run_id}:{uuid.uuid4().hex}"}}
    root = run_control.root if run_control else None
    with (
        workflow_checkpointer(run_id, root) as checkpointer,
        RunResources() as resources,
    ):
        graph = build_minimal_graph(
            provider,
            progress_callback=progress_callback,
            run_control=run_control,
            blueprint_max_attempts=blueprint_max_attempts,
            content_reflection_max_attempts=content_reflection_max_attempts,
            fact_check_max_corrections=fact_check_max_corrections,
            fact_check_max_claims_per_unit=fact_check_max_claims_per_unit,
            fact_check_web_enabled=fact_check_web_enabled,
            interactive_component_max_attempts=interactive_component_max_attempts,
            interactive_component_sandbox_runner=interactive_component_sandbox_runner,
            checkpointer=checkpointer,
        )
        if resume_checkpoint:
            config = resume_checkpoint["config"]
            snapshot = graph.get_state(config)
            validate_prompt_profile(snapshot.values.get("prompt_profile"))
            value = (
                Command(resume=True)
                if any(task.interrupts for task in snapshot.tasks)
                else None
            )
        else:
            value = initial
        state = graph.invoke(value, config, context=resources, durability="sync")
        if state.get("__interrupt__"):
            raise RunStopped("user_requested_stop")
        if run_control:
            run_control.raise_if_stopped()
        persist_workflow_snapshots(state)
        return state
