"""Filesystem snapshots for completed workflow runs.

The workflow remains usable without a database (which keeps the graph easy to
test), but every completed run also gets immutable JSON snapshots.  The API
indexes these snapshots in SQLite and uses the paths below for later reads.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from annotation.config import STORAGE_DIR


def _safe_run_id(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip(".-") or "run"


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


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

    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _blueprint_loop_payload(state: Mapping[str, Any]) -> dict[str, Any] | None:
    trace = state.get("blueprint_loop_trace") or state.get("blueprint_trace")
    if trace is None:
        return None
    payload = _model_dump(trace)
    return dict(payload) if isinstance(payload, Mapping) else None


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


def _blueprint_loop_summary(
    state: Mapping[str, Any],
    *,
    trace_path: Path | None = None,
) -> dict[str, Any] | None:
    payload = _blueprint_loop_payload(state)
    if payload is None:
        return None
    attempts = payload.get("attempts") or []
    path_value = (
        state.get("blueprint_trace_path")
        or state.get("blueprint_loop_trace_path")
        or payload.get("artifact_path")
        or (str(trace_path.resolve()) if trace_path else None)
    )
    return {
        "trace_id": payload.get("trace_id"),
        "attempt_count": len(attempts),
        "max_attempts": payload.get("max_attempts"),
        "final_status": payload.get("final_status"),
        "stop_reason": payload.get("stop_reason"),
        "trace_path": path_value,
    }


def write_blueprint_artifact(state: Mapping[str, Any], *, root: str | Path | None = None) -> Path | None:
    """Write the Blueprint, quality gate and optional revision-loop trace.

    A failed provider call may not yield a Blueprint at all, but its trace is
    still an auditable artifact. Legacy callers without a trace retain the
    original v1 payload shape.
    """

    blueprint = state.get("blueprint")
    loop_trace = _blueprint_loop_payload(state)
    if blueprint is None and loop_trace is None:
        return None
    run_id = str(state.get("run_id") or (blueprint.run_id if blueprint is not None else loop_trace.get("run_id", "run")))
    version = getattr(blueprint, "version", 1)
    path = _immutable_path(Path(root or (STORAGE_DIR / "artifacts" / "blueprint")) / _safe_run_id(run_id) / f"blueprint-v{version}.json")
    if loop_trace is not None:
        loop_trace["artifact_path"] = str(path.resolve())
        # Keep the in-memory typed trace and the serialized trace identical.
        trace_object = state.get("blueprint_loop_trace") or state.get("blueprint_trace")
        if trace_object is not None and hasattr(trace_object, "artifact_path"):
            trace_object.artifact_path = str(path.resolve())
    check = state.get("blueprint_check")
    payload: dict[str, Any] = {
        "schema_version": "blueprint-artifact-v2" if loop_trace is not None else "blueprint-artifact-v1",
        "blueprint": _model_dump(blueprint) if blueprint is not None else None,
        "blueprint_check": _model_dump(check) if check is not None else None,
    }
    if loop_trace is not None:
        loop_trace["artifact_path"] = str(path.resolve())
        payload["loop_trace"] = loop_trace
    return _write_json(
        path,
        payload,
    )


def write_document_artifact(state: Mapping[str, Any], *, root: str | Path | None = None) -> Path | None:
    """Write the assembled Document IR and its review linkage."""

    document = state.get("document")
    if document is None:
        return None
    run_id = str(state.get("run_id") or document.run_id)
    path = _immutable_path(Path(root or (STORAGE_DIR / "artifacts" / "document")) / _safe_run_id(run_id) / f"document-v{document.version}.json")
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
    path = _immutable_path(Path(root or (STORAGE_DIR / "artifacts" / "quiz")) / _safe_run_id(run_id) / "quiz-v1.json")
    payload = {
        "schema_version": "quiz-artifact-v1",
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
        Path(root or (STORAGE_DIR / "artifacts" / "fact_check"))
        / _safe_run_id(run_id)
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


def write_run_manifest(
    state: Mapping[str, Any],
    *,
    blueprint_path: Path | None,
    document_path: Path | None,
    fact_check_path: Path | None = None,
    root: str | Path | None = None,
) -> Path:
    """Write a compact index of the run without duplicating large source text."""

    run_id = str(state.get("run_id") or "run")
    source_document = state.get("source_document")
    report = state.get("review_report")
    loop_summary = _blueprint_loop_summary(state, trace_path=blueprint_path)
    content_loop_summary = _content_loop_summary(state)
    fact_check_summary = _fact_check_summary(state)
    fact_check_path_value = state.get("fact_check_artifact_path") or (
        str(fact_check_path.resolve()) if fact_check_path else None
    )
    manifest = {
        "schema_version": (
            "run-manifest-v3"
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
    path = _immutable_path(Path(root or (STORAGE_DIR / "artifacts" / "runs")) / _safe_run_id(run_id) / "run.json")
    return _write_json(path, manifest)


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
    if blueprint_path and _blueprint_loop_payload(state) is not None:
        state["blueprint_trace_path"] = str(blueprint_path.resolve())
        state["blueprint_loop_trace_path"] = str(blueprint_path.resolve())
    manifest_path = write_run_manifest(
        state,
        blueprint_path=blueprint_path,
        document_path=document_path,
        fact_check_path=fact_check_path,
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
    "write_quiz_artifact",
    "write_run_manifest",
]
