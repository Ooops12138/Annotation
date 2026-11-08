"""Source ingestion and the single hierarchical Blueprint workflow stage."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from annotation.domain.textbook import HierarchicalBlueprintTrace
from annotation.ingestion.pdf_parser import extraction_warnings, parse_pdf
from annotation.ingestion.structure import build_textbook_structure
from annotation.providers import ProviderError
from annotation.workflow.blueprint_checks import validate_blueprint
from annotation.workflow.blueprint_hierarchy import BlueprintEngine, build_blueprint_windows
from annotation.workflow.budget import PromptBudget
from annotation.workflow.content_support import merge_messages
from annotation.workflow.context import WorkflowContext
from annotation.workflow.helpers import default_pdf
from annotation.workflow.models import WorkflowState


def ingest(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    run_id = state.get("run_id", f"run-{uuid.uuid4().hex[:10]}")
    pdf_path = Path(state.get("pdf_path") or default_pdf())
    source_document, blocks = parse_pdf(pdf_path, run_id=run_id)
    warnings = extraction_warnings(blocks)
    return {
        "run_id": run_id,
        "pdf_path": str(pdf_path),
        "source_document": source_document,
        "source_blocks": blocks,
        "source_refs": [block.source_ref for block in blocks],
        "warnings": warnings,
    }


def prepare_blueprint(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    structure = build_textbook_structure(state["source_document"], state["source_blocks"])
    result: dict[str, Any] = {
        "textbook_structure": structure,
        "warnings": merge_messages(state.get("warnings"), structure.warnings),
    }
    if state.get("blueprint_preloaded"):
        return result
    try:
        result["blueprint_windows"] = build_blueprint_windows(
            structure, state["source_blocks"], PromptBudget.for_provider(ctx.provider))
    except ProviderError as exc:
        result["blueprint_hierarchy_trace"] = HierarchicalBlueprintTrace(stop_reason=str(exc))
    return result


def hierarchical_blueprint(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    if state.get("blueprint_preloaded"):
        blueprint = state["blueprint"]
        check = validate_blueprint(blueprint, valid_source_refs=set(state["source_refs"]))
        status = "accepted" if check.status == "accepted" else "blocked"
        return {
            "blueprint": blueprint.model_copy(update={"status": check.status, "issues": check.issues}),
            "blueprint_check": check,
            "blueprint_hierarchy_trace": HierarchicalBlueprintTrace(
                final_status=status, stop_reason="preloaded"),
            "workflow_status": status,
            "blueprint_route": "accept" if status == "accepted" else "block",
        }
    engine = BlueprintEngine(ctx.provider, state["source_document"], state["source_blocks"],
        state["textbook_structure"], max_attempts=ctx.blueprint_max_attempts)
    trace = state.get("blueprint_hierarchy_trace")
    if trace is not None:
        blueprint = None
    else:
        blueprint = engine.run(state.get("blueprint_windows", []))
        trace = engine.trace
    status = trace.final_status
    result: dict[str, Any] = {
        "blueprint_hierarchy_trace": trace,
        "workflow_status": status,
        "blueprint_route": "accept" if status == "accepted" else "fail" if status == "failed" else "block",
    }
    if blueprint is not None:
        result["blueprint"] = blueprint
        result["blueprint_check"] = validate_blueprint(blueprint, valid_source_refs=set(state["source_refs"]))
    else:
        result["errors"] = merge_messages(state.get("errors"), [trace.stop_reason])
    return result


def record_blocked_run(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    return {"workflow_status": "blocked"}


def record_failed_run(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    return {"workflow_status": "failed"}
