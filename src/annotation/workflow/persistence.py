"""Filesystem snapshots for completed workflow runs.

The workflow remains usable without a database (which keeps the graph easy to
test), but every completed run also gets immutable JSON snapshots.  The API
indexes these snapshots in SQLite and uses the paths below for later reads.
"""

from __future__ import annotations

import re
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping

from annotation.config import STORAGE_DIR
from annotation.artifact_storage import artifact_directory, update_run_info
from annotation.prompt_archive import write_artifact_json


def _safe_run_id(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip(".-") or "run"


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    return write_artifact_json(path, dict(payload))


def _immutable_path(path: Path) -> Path:
    """Keep prior snapshots when a caller intentionally reuses a run id."""

    if not path.exists():
        return path
    revision = 2
    while True:
        candidate = path.with_name(f"{path.stem}-revision-{revision}{path.suffix}")
        if not candidate.exists():
            return candidate
        revision += 1


def _model_dump(value: Any) -> Any:
    """Serialize Pydantic artifacts while accepting test-friendly mappings."""

    if isinstance(value, type):
        return value.__name__
    if is_dataclass(value):
        return {field.name: _model_dump(getattr(value, field.name)) for field in fields(value)}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {key: _model_dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_model_dump(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _content_loop_traces(state: Mapping[str, Any]) -> list[Any]:
    values = state.get("content_loop_traces") or []
    return [_model_dump(value) for value in values]


def _content_loop_summary(state: Mapping[str, Any], *, trace_path: Path | None = None) -> dict[str, Any] | None:
    summary = state.get("content_loop_summary")
    if summary is not None:
        payload = dict(_model_dump(summary)) if isinstance(_model_dump(summary), Mapping) else {}
    else:
        traces = _content_loop_traces(state)
        if not traces:
            return None
        statuses = [str(item.get("final_status") or item.get("status") or "") for item in traces]
        payload = {
            "unit_count": len(traces),
            "accepted_count": sum(status == "accepted" for status in statuses),
            "blocked_count": sum(status == "blocked" for status in statuses),
            "failed_count": sum(status == "failed" for status in statuses),
            "attempt_count": sum(len(item.get("attempts") or []) for item in traces),
            "final_status": "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "accepted",
        }
    status = state.get("content_loop_status")
    if status and not payload.get("final_status"):
        payload["final_status"] = status
    path = state.get("content_loop_trace_path") or payload.get("trace_path") or (str(trace_path.resolve()) if trace_path else None)
    if path:
        payload["trace_path"] = path
    return payload


def _fact_check_summary(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the compact A-003 result without duplicating its full trace."""

    summary = state.get("fact_check_summary")
    artifact = state.get("fact_check_artifact")
    artifact_payload = _model_dump(artifact) if artifact is not None else None
    if summary is None and isinstance(artifact_payload, Mapping):
        summary = artifact_payload.get("summary")
    payload = _model_dump(summary) if summary is not None else None
    result = dict(payload) if isinstance(payload, Mapping) else {}
    status = state.get("fact_check_status")
    if not status and isinstance(artifact_payload, Mapping):
        status = artifact_payload.get("status")
    if status and not result.get("final_status"):
        result["final_status"] = status
    return result or None


def _interactive_component_summary(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the compact A-004 result without duplicating its full trace."""

    summary = state.get("interactive_component_summary")
    trace = state.get("interactive_component_loop_trace")
    payload = _model_dump(summary) if summary is not None else None
    result = dict(payload) if isinstance(payload, Mapping) else {}
    if not result and trace is not None:
        trace_payload = _model_dump(trace)
        if isinstance(trace_payload, Mapping):
            result = {
                "component_count": len(state.get("interactive_component_artifacts", [])),
                "attempt_count": len(trace_payload.get("attempts") or []),
                "final_status": trace_payload.get("final_status"),
                "stop_reason": trace_payload.get("stop_reason"),
            }
    status = state.get("interactive_component_status")
    if status and not result.get("final_status"):
        result["final_status"] = status
    path = state.get("interactive_component_artifact_path") or result.get("trace_path")
    if path:
        result["trace_path"] = path
    return result or None


def _blueprint_loop_summary(
    state: Mapping[str, Any],
    *,
    trace_path: Path | None = None,
) -> dict[str, Any] | None:
    hierarchy = state.get("blueprint_hierarchy_trace")
    if hierarchy is not None:
        trace = _model_dump(hierarchy)
        calls = trace.get("calls", [])
        return {"mode": "hierarchical", "attempt_count": len(calls),
            "model_call_count": sum(c.get("model_called", False) for c in calls),
            "cache_hit_count": sum(c.get("cache_hit", False) for c in calls),
            "window_count": len(trace.get("windows", [])),
            "accepted_window_count": sum(w.get("status") in {"accepted", "skipped"} for w in trace.get("windows", [])),
            "final_status": trace.get("final_status"), "stop_reason": trace.get("stop_reason"),
            "trace_path": state.get("blueprint_trace_path") or (str(trace_path.resolve()) if trace_path else None)}
    return None


def write_blueprint_artifact(state: Mapping[str, Any], *, root: str | Path | None = None) -> Path | None:
    """Write the Blueprint and the unified hierarchical trace, including failed runs."""
    blueprint = state.get("blueprint")
    hierarchy = state.get("blueprint_hierarchy_trace")
    if blueprint is None and hierarchy is None:
        return None
    run_id = str(state.get("run_id") or blueprint.run_id)
    version = getattr(blueprint, "version", 1)
    path = _immutable_path(artifact_directory(run_id, "blueprint", root) / f"blueprint-v{version}.json")
    check = state.get("blueprint_check")
    return _write_json(path, {
        "schema_version": "blueprint-artifact-v3",
        "blueprint": _model_dump(blueprint),
        "blueprint_check": _model_dump(check),
        "textbook_structure": _model_dump(state.get("textbook_structure")),
        "hierarchy_trace": _model_dump(hierarchy),
    })


def write_document_artifact(state: Mapping[str, Any], *, root: str | Path | None = None) -> Path | None:
    """Write the assembled Document IR and its review linkage."""

    document = state.get("document")
    if document is None:
        return None
    run_id = str(state.get("run_id") or document.run_id)
    path = _immutable_path(artifact_directory(run_id, "document", root) / f"document-v{document.version}.json")
    report = state.get("review_report")
    return _write_json(
        path,
        {
            "schema_version": "document-artifact-v1",
            "document": document.model_dump(mode="json"),
            "review_report_id": report.report_id if report is not None else document.review_report_id,
        },
    )


def write_quiz_artifact(state: Mapping[str, Any], *, root: str | Path | None = None) -> Path | None:
    """Persist quiz artifacts separately from prose content artifacts."""

    artifacts = list(state.get("quiz_artifacts", []))
    coverage = state.get("quiz_coverage_report")
    if not artifacts and coverage is None:
        return None
    run_id = str(state.get("run_id") or "run")
    path = _immutable_path(artifact_directory(run_id, "quiz", root) / "quiz-v1.json")
    payload = {
        "schema_version": "quiz-artifact-v2",
        "run_id": run_id,
        "quiz_artifacts": [_model_dump(artifact) for artifact in artifacts],
        "quiz_coverage": _model_dump(coverage) if coverage is not None else None,
    }
    return _write_json(path, payload)


def write_fact_check_artifact(state: Mapping[str, Any], *, root: str | Path | None = None) -> Path | None:
    """Persist the optional A-003 audit trace apart from learner artifacts."""

    artifact = state.get("fact_check_artifact")
    summary = _fact_check_summary(state)
    if artifact is None and summary is None:
        return None
    artifact_payload = _model_dump(artifact) if artifact is not None else None
    run_id = str(
        state.get("run_id")
        or (artifact_payload.get("run_id") if isinstance(artifact_payload, Mapping) else None)
        or "run"
    )
    version = (
        artifact_payload.get("version", 1)
        if isinstance(artifact_payload, Mapping)
        else 1
    )
    path = _immutable_path(
        artifact_directory(run_id, "fact_check", root)
        / f"fact-check-v{version}.json"
    )
    artifact_path = str(path.resolve())
    if artifact is not None and hasattr(artifact, "artifact_path"):
        artifact.artifact_path = artifact_path
        artifact_payload = _model_dump(artifact)
    elif isinstance(artifact, dict):
        artifact["artifact_path"] = artifact_path
        artifact_payload = _model_dump(artifact)
    payload = {
        "schema_version": "fact-check-artifact-v1",
        "run_id": run_id,
        "status": state.get("fact_check_status") or (
            artifact_payload.get("status") if isinstance(artifact_payload, Mapping) else None
        ),
        "summary": summary,
        "fact_check": artifact_payload,
    }
    return _write_json(path, payload)


def write_interactive_component_artifact(state: Mapping[str, Any], *, root: str | Path | None = None) -> Path | None:
    """Persist the complete A-004 plan, candidates, browser evidence and trace."""

    trace = state.get("interactive_component_loop_trace")
    summary = _interactive_component_summary(state)
    plan = state.get("interactive_component_plan")
    artifacts = list(state.get("interactive_component_artifacts", []))
    if trace is None and summary is None and plan is None and not artifacts:
        return None
    trace_payload = _model_dump(trace) if trace is not None else None
    run_id = str(
        state.get("run_id")
        or (trace_payload.get("run_id") if isinstance(trace_payload, Mapping) else None)
        or getattr(plan, "run_id", None)
        or "run"
    )
    path = _immutable_path(
        artifact_directory(run_id, "interactive_components", root)
        / "interactive-components-v2.json"
    )
    artifact_path = str(path.resolve())
    if trace is not None and hasattr(trace, "artifact_path"):
        trace.artifact_path = artifact_path
        trace_payload = _model_dump(trace)
    elif isinstance(trace, dict):
        trace["artifact_path"] = artifact_path
        trace_payload = _model_dump(trace)
    for artifact in artifacts:
        if hasattr(artifact, "artifact_path"):
            artifact.artifact_path = artifact_path
        elif isinstance(artifact, dict):
            artifact["artifact_path"] = artifact_path
    payload = {
        "schema_version": "interactive-components-v2",
        "run_id": run_id,
        "status": state.get("interactive_component_status") or (
            trace_payload.get("final_status") if isinstance(trace_payload, Mapping) else None
        ),
        "summary": summary,
        "plan": _model_dump(plan) if plan is not None else None,
        "task": _model_dump(state.get("interactive_component_task")) if state.get("interactive_component_task") is not None else None,
        "interactive_components": [_model_dump(artifact) for artifact in artifacts],
        "loop_trace": trace_payload,
        "unit_results": _model_dump(state.get("interactive_component_results", [])),
    }
    return _write_json(path, payload)


def write_run_manifest(
    state: Mapping[str, Any],
    *,
    blueprint_path: Path | None,
    document_path: Path | None,
    fact_check_path: Path | None = None,
    interactive_component_path: Path | None = None,
    root: str | Path | None = None,
) -> Path:
    """Write a compact index of the run without duplicating large source text."""

    run_id = str(state.get("run_id") or "run")
    source_document = state.get("source_document")
    report = state.get("review_report")
    loop_summary = _blueprint_loop_summary(state, trace_path=blueprint_path)
    content_loop_summary = _content_loop_summary(state)
    fact_check_summary = _fact_check_summary(state)
    interactive_component_summary = _interactive_component_summary(state)
    fact_check_path_value = state.get("fact_check_artifact_path") or (
        str(fact_check_path.resolve()) if fact_check_path else None
    )
    manifest = {
        "schema_version": (
            "run-manifest-v4"
            if interactive_component_summary is not None or interactive_component_path is not None
            else "run-manifest-v3"
            if fact_check_summary is not None or fact_check_path_value
            else "run-manifest-v2" if content_loop_summary is not None else "run-manifest-v1"
        ),
        "run_id": run_id,
        "pdf_path": state.get("pdf_path"),
        "source_document_id": source_document.artifact_id if source_document is not None else None,
        "blueprint_artifact_id": state.get("blueprint").artifact_id if state.get("blueprint") is not None else None,
        "document_id": state.get("document").document_id if state.get("document") is not None else None,
        "review_report_id": report.report_id if report is not None else None,
        "document_status": state.get("document").status if state.get("document") is not None else None,
        "review_status": report.status if report is not None else None,
        "provider_metadata": state.get("provider_metadata", {}),
        "source_block_count": len(state.get("source_blocks", [])),
        "content_artifact_count": len(state.get("content_artifacts", [])),
        "quiz_artifact_count": len(state.get("quiz_artifacts", [])),
        "interactive_component_count": len(state.get("interactive_component_artifacts", [])),
        "quiz_coverage_status": (
            getattr(state.get("quiz_coverage_report"), "status", None)
            if state.get("quiz_coverage_report") is not None
            else None
        ),
        "paths": {
            "source_json": source_document.run_metadata.get("json_path") if source_document is not None else None,
            "source_markdown": source_document.run_metadata.get("markdown_path") if source_document is not None else None,
            "blueprint": str(blueprint_path.resolve()) if blueprint_path else None,
            "content": state.get("content_artifact_path"),
            "quiz": state.get("quiz_artifact_path"),
            "document": str(document_path.resolve()) if document_path else None,
            "review": state.get("review_report_path"),
        },
        "errors": list(state.get("errors", [])),
        "warnings": list(state.get("warnings", [])),
        # Full prompts, raw output and per-attempt checks stay in the Blueprint
        # artifact; this compact summary is safe for API indexing.
        "blueprint_loop": loop_summary,
        "content_loop": content_loop_summary,
    }
    if fact_check_summary is not None or fact_check_path_value:
        manifest["paths"]["fact_check"] = fact_check_path_value
        manifest["fact_check_summary"] = fact_check_summary
    interactive_component_path_value = state.get("interactive_component_artifact_path") or (
        str(interactive_component_path.resolve()) if interactive_component_path else None
    )
    if interactive_component_summary is not None or interactive_component_path_value:
        manifest["paths"]["interactive_components"] = interactive_component_path_value
        manifest["interactive_component_summary"] = interactive_component_summary
    path = _immutable_path(artifact_directory(run_id, "manifest", root) / ("run.json" if root is not None else "manifest.json"))
    written = _write_json(path, manifest)
    if root is None:
        source = state.get("source_document")
        update_run_info(run_id, status=state.get("workflow_status", "completed"),
                        paths={**manifest["paths"], "manifest": str(written.resolve())},
                        errors=manifest["errors"], **{key: state.get("provider_metadata", {}).get(key, "") for key in ("provider", "model")})
    return written


def persist_workflow_snapshots(state: dict[str, Any]) -> dict[str, Any]:
    """Persist all small workflow snapshots and return their paths."""

    blueprint_path = write_blueprint_artifact(state)
    content_traces = _content_loop_traces(state)
    if content_traces:
        content_path = state.get("content_loop_trace_path") or state.get("content_artifact_path")
        if content_path:
            state["content_loop_trace_path"] = str(Path(content_path).resolve())
            summary = state.get("content_loop_summary")
            if isinstance(summary, Mapping):
                summary["trace_path"] = state["content_loop_trace_path"]
    document_path = write_document_artifact(state)
    quiz_path = write_quiz_artifact(state)
    state["quiz_artifact_path"] = str(quiz_path.resolve()) if quiz_path else ""
    fact_check_path = write_fact_check_artifact(state)
    state["fact_check_artifact_path"] = str(fact_check_path.resolve()) if fact_check_path else ""
    interactive_component_path = write_interactive_component_artifact(state)
    state["interactive_component_artifact_path"] = str(interactive_component_path.resolve()) if interactive_component_path else ""
    if blueprint_path and state.get("blueprint_hierarchy_trace") is not None:
        state["blueprint_trace_path"] = str(blueprint_path.resolve())
    manifest_path = write_run_manifest(
        state,
        blueprint_path=blueprint_path,
        document_path=document_path,
        fact_check_path=fact_check_path,
        interactive_component_path=interactive_component_path,
    )
    state["blueprint_artifact_path"] = str(blueprint_path.resolve()) if blueprint_path else ""
    state["document_artifact_path"] = str(document_path.resolve()) if document_path else ""
    state["run_manifest_path"] = str(manifest_path.resolve())
    return state


__all__ = [
    "persist_workflow_snapshots",
    "write_blueprint_artifact",
    "write_document_artifact",
    "write_fact_check_artifact",
    "write_interactive_component_artifact",
    "write_quiz_artifact",
    "write_run_manifest",
]
