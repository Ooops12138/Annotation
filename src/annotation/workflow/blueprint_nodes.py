"""Workflow stage nodes with explicit configuration and returned state updates."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from annotation.domain.artifacts import BlueprintAttemptTrace, BlueprintLoopTrace
from annotation.prompt_loader import load_prompt
from annotation.providers import ProviderError, StructuredGenerationRequest
from annotation.workflow.blueprint_checks import validate_blueprint
from annotation.ingestion.pdf_parser import extraction_warnings, parse_pdf
from annotation.ingestion.structure import build_textbook_structure
from annotation.domain.textbook import HierarchicalBlueprintTrace
from annotation.workflow.budget import PromptBudget
from annotation.workflow.blueprint_hierarchy import (
    BlueprintEngine,
    build_blueprint_windows,
)
from annotation.workflow.content_support import merge_messages
from annotation.workflow.helpers import (
    blueprint_from_draft,
    default_pdf,
    response_metadata,
    provider_metadata,
    source_context,
)
from annotation.workflow.models import BlueprintDraft, WorkflowState

BLUEPRINT_GENERATION_PROMPT_VERSION = "load_or_create_blueprint:v4:program-unit-ids"
BLUEPRINT_REVISION_PROMPT_VERSION = "revise_blueprint:v4:program-unit-ids"
from annotation.workflow.context import WorkflowContext
import logging

logger = logging.getLogger(__name__)


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
    structure = build_textbook_structure(
        state["source_document"], state["source_blocks"]
    )
    result: dict[str, Any] = {"textbook_structure": structure}
    if state.get("blueprint_preloaded"):
        return {**result, "blueprint_mode": "single"}
    workflow_capabilities = getattr(ctx.provider, "workflow", None)
    fixture = bool(
        workflow_capabilities is not None
        and workflow_capabilities.use_blueprint_fixture(ctx.provider)
    )
    if fixture:
        return {**result, "blueprint_mode": "fixture"}
    result["warnings"] = merge_messages(state.get("warnings"), structure.warnings)
    budget = PromptBudget.for_provider(ctx.provider)
    try:
        windows = build_blueprint_windows(structure, state["source_blocks"], budget)
    except ProviderError as exc:
        return {
            **result,
            "blueprint_mode": "hierarchical",
            "blueprint_windows": [],
            "blueprint_hierarchy_trace": HierarchicalBlueprintTrace(
                stop_reason=str(exc)
            ),
        }
    prompt = (
        load_prompt(
            "load_or_create_blueprint",
            TEXTBOOK_CONTEXT=source_context(state["source_blocks"]),
        )
        if len(windows) == 1
        else ""
    )
    single = (
        len(windows) == 1
        and len(structure.nodes) == 1
        and budget.fits(prompt, BlueprintDraft)
    )
    return {
        **result,
        "blueprint_windows": windows,
        "blueprint_mode": "single" if single else "hierarchical",
    }


def hierarchical_blueprint(
    ctx: WorkflowContext, state: WorkflowState
) -> dict[str, Any]:
    engine = BlueprintEngine(
        ctx.provider,
        state["source_document"],
        state["source_blocks"],
        state["textbook_structure"],
        max_attempts=ctx.blueprint_max_attempts,
    )
    existing = state.get("blueprint_hierarchy_trace")
    if existing and existing.stop_reason != "not_started":
        trace, blueprint = existing, None
    else:
        blueprint = engine.run(state.get("blueprint_windows", []))
        trace = engine.trace
    status = trace.final_status
    result: dict[str, Any] = {
        "blueprint_hierarchy_trace": trace,
        "workflow_status": status,
        "blueprint_route": "accept"
        if status == "accepted"
        else "fail"
        if status == "failed"
        else "block",
    }
    if blueprint is not None:
        result["blueprint"] = blueprint
        result["blueprint_check"] = validate_blueprint(
            blueprint, valid_source_refs=set(state["source_refs"])
        )
    else:
        result["errors"] = merge_messages(state.get("errors"), [trace.stop_reason])
    return result


def generate_blueprint_attempt(
    ctx: WorkflowContext, state: WorkflowState
) -> dict[str, Any]:
    """Make exactly one Blueprint model call for this graph visit."""

    if state.get("blueprint_preloaded"):
        return {
            "blueprint_attempt": 0,
            "blueprint_generation_status": "succeeded",
            "blueprint_generation_prompt": "",
            "blueprint_generation_raw_output": "",
            "blueprint_generation_parsed_output": None,
            "blueprint_generation_error": "",
            "blueprint_generation_error_category": "",
            "blueprint_generation_retryable": False,
        }
    attempt = int(state.get("blueprint_attempt", 0)) + 1
    previous = state.get("blueprint")
    if attempt == 1:
        prompt = load_prompt(
            "load_or_create_blueprint",
            TEXTBOOK_CONTEXT=source_context(state["source_blocks"]),
        )
        agent = "load_or_create_blueprint"
    else:
        check = state.get("blueprint_check")
        prompt = load_prompt(
            "revise_blueprint",
            TEXTBOOK_CONTEXT=source_context(state["source_blocks"]),
            PREVIOUS_BLUEPRINT=json.dumps(
                previous.model_dump(mode="json") if previous is not None else {},
                ensure_ascii=False,
                indent=2,
            ),
            CHECK_RESULT=json.dumps(
                check.model_dump(mode="json") if check is not None else {},
                ensure_ascii=False,
                indent=2,
            ),
            REVIEW_ISSUES=json.dumps(
                {
                    "issues": [
                        issue.model_dump(mode="json")
                        for issue in state.get("blueprint_feedback", [])
                    ],
                    "generation_error": state.get("blueprint_generation_error") or None,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        agent = "revise_blueprint"
    result: dict[str, Any] = {
        "blueprint_attempt": attempt,
        "blueprint_generation_prompt": prompt,
        "blueprint_generation_raw_output": "",
        "blueprint_generation_parsed_output": None,
        "blueprint_generation_error": "",
        "blueprint_generation_error_category": "",
        "blueprint_generation_retryable": False,
    }
    try:
        budget = PromptBudget.for_provider(ctx.provider)
        if state.get("blueprint_mode") != "fixture":
            budget.check(prompt, BlueprintDraft)
        response = ctx.provider.generate_structured(
            StructuredGenerationRequest(
                prompt=prompt,
                schema=BlueprintDraft,
                # Blueprint JSON can be large for a full chapter. Keep this
                # configurable and high enough to avoid truncating arrays.
                max_output_tokens=budget.output_tokens,
                metadata={
                    "agent": agent,
                    "prompt_version": BLUEPRINT_GENERATION_PROMPT_VERSION if agent == "load_or_create_blueprint" else BLUEPRINT_REVISION_PROMPT_VERSION,
                    "run_id": state["run_id"],
                    "attempt": attempt,
                },
            )
        )
        draft = BlueprintDraft.model_validate(response.value.model_dump(mode="python"))
        workflow_capabilities = getattr(ctx.provider, "workflow", None)
        blueprint, adapted = blueprint_from_draft(
            draft=draft,
            state=state,
            provider_name=response.provider,
            workflow_capabilities=workflow_capabilities,
        )
        result.update(
            {
                "blueprint": blueprint,
                "blueprint_generation_status": "succeeded",
                "blueprint_generation_raw_output": response.raw_text,
                "blueprint_generation_parsed_output": draft.model_dump(mode="json"),
                "provider_metadata": {
                    **response_metadata(response),
                    **({"fixture_adaptation": "empty_source_refs"} if adapted else {}),
                },
            }
        )
        return result
    except ProviderError as exc:
        category = getattr(exc, "category", "provider")
        parsed_output = getattr(exc, "parsed_output", None)
        if hasattr(parsed_output, "model_dump"):
            parsed_output = parsed_output.model_dump(mode="json")
        elif parsed_output is not None and not isinstance(parsed_output, dict):
            parsed_output = None
        result.update(
            {
                "blueprint_generation_status": "schema_error"
                if category == "schema"
                else "provider_error",
                "blueprint_generation_raw_output": str(
                    getattr(exc, "raw_output", "") or ""
                ),
                "blueprint_generation_parsed_output": parsed_output,
                "blueprint_generation_error": str(exc),
                "blueprint_generation_error_category": category,
                "blueprint_generation_retryable": bool(
                    getattr(exc, "retryable", False) or category == "schema"
                ),
                "provider_metadata": provider_metadata(ctx.provider),
            }
        )
        return result
    except Exception as exc:
        logger.exception("Unexpected blueprint generation failure")
        result.update(
            {
                "blueprint_generation_status": "provider_error",
                "blueprint_generation_error": str(exc),
                "blueprint_generation_error_category": "runtime",
                "provider_metadata": provider_metadata(ctx.provider),
            }
        )
        return result


def check_blueprint_attempt(
    ctx: WorkflowContext, state: WorkflowState
) -> dict[str, Any]:
    if state.get("blueprint_preloaded"):
        blueprint = state.get("blueprint")
        if blueprint is None:
            return {"blueprint_check": None, "blueprint_feedback": []}
        check = validate_blueprint(
            blueprint, valid_source_refs=set(state["source_refs"])
        )
    elif (
        state.get("blueprint_generation_status") != "succeeded"
        or state.get("blueprint") is None
    ):
        return {"blueprint_check": None, "blueprint_feedback": []}
    else:
        blueprint = state["blueprint"]
        check = validate_blueprint(
            blueprint, valid_source_refs=set(state["source_refs"])
        )
    blueprint = blueprint.model_copy(
        update={
            "issues": list(check.issues),
            "status": "needs_revision"
            if check.status == "needs_revision"
            else "accepted",
        }
    )
    return {
        "blueprint": blueprint,
        "blueprint_check": check,
        "blueprint_feedback": list(check.issues),
    }


def route_blueprint(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    attempt = int(state.get("blueprint_attempt", 0))
    preloaded = bool(state.get("blueprint_preloaded"))
    status = state.get("blueprint_generation_status", "provider_error")
    check = state.get("blueprint_check")
    if preloaded:
        route = (
            "accept" if check is not None and check.status == "accepted" else "block"
        )
        stop_reason = "preloaded"
        final_status = "accepted" if route == "accept" else "blocked"
    elif status == "provider_error" and not state.get("blueprint_generation_retryable"):
        route, stop_reason, final_status = (
            "fail",
            "non_retryable_provider_error",
            "failed",
        )
    elif status != "succeeded" or check is None or check.status == "needs_revision":
        if attempt < ctx.blueprint_max_attempts:
            route, stop_reason, final_status = "revise", None, None
        else:
            route, stop_reason, final_status = "block", "max_attempts", "blocked"
    else:
        route, stop_reason, final_status = "accept", "accepted", "accepted"

    attempts = list(state.get("blueprint_attempts", []))
    if not preloaded:
        attempts.append(
            BlueprintAttemptTrace(
                attempt=attempt,
                prompt=str(state.get("blueprint_generation_prompt", "")),
                raw_output=str(state.get("blueprint_generation_raw_output", "")),
                parsed_output=state.get("blueprint_generation_parsed_output"),
                generation_status=status
                if status in {"succeeded", "schema_error", "provider_error"}
                else "provider_error",
                error=state.get("blueprint_generation_error") or None,
                error_category=state.get("blueprint_generation_error_category") or None,
                provider_metadata=dict(state.get("provider_metadata", {})),
                check=check,
                feedback=list(state.get("blueprint_feedback", [])),
                route=route,
                stop_reason=stop_reason,
            )
        )
    result: dict[str, Any] = {
        "blueprint_attempts": attempts,
        "blueprint_route": route,
        "blueprint_generation_prompt": "",
        "blueprint_generation_raw_output": "",
        "blueprint_generation_parsed_output": None,
    }
    if final_status is not None:
        trace = BlueprintLoopTrace(
            trace_id=str(
                state.get("blueprint_trace_id") or f"trace-{uuid.uuid4().hex[:12]}"
            ),
            run_id=state["run_id"],
            max_attempts=ctx.blueprint_max_attempts,
            attempts=attempts,
            final_status=final_status,
            final_attempt=attempt,
            stop_reason=stop_reason or "max_attempts",
            final_blueprint_artifact_id=state.get("blueprint").artifact_id
            if state.get("blueprint")
            else None,
        )
        result["blueprint_loop_trace"] = trace
        result["workflow_status"] = final_status
    return result


def record_blocked_run(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    return {"workflow_status": "blocked"}


def record_failed_run(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    error = state.get("blueprint_generation_error") or "Blueprint loop failed"
    return {
        "workflow_status": "failed",
        "errors": merge_messages(
            state.get("errors"), [f"blueprint_loop_failed: {error}"]
        ),
    }
