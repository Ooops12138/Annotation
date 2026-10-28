"""Application service for durable library and workflow records."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

from annotation.config import first_book_pdf
from annotation.prompt_archive import read_artifact_json
from annotation.domain.artifacts import LearningDocument, ReviewReport
from annotation.persistence import DEFAULT_DATABASE_PATH, PersistenceRepository, project_document_status
from annotation.retrieval import index_source_blocks


logger = logging.getLogger(__name__)

_ready_lock = Lock()
_ready = False


def repository() -> PersistenceRepository:
    """Open the shared local database and register the local development textbook once."""

    global _ready
    repo = PersistenceRepository(DEFAULT_DATABASE_PATH)
    if not _ready:
        with _ready_lock:
            if not _ready:
                _register_seed_book(repo)
                _ready = True
    return repo


def _register_seed_book(repo: PersistenceRepository) -> dict[str, Any] | None:
    try:
        return repo.register_book(first_book_pdf())
    except FileNotFoundError:
        return None


def _load_json(path: str | Path) -> dict[str, Any]:
    return read_artifact_json(Path(path))


def _document_from_path(path: str | Path) -> dict[str, Any]:
    """Validate the root only; local defects belong to the reading projection."""
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != "document-artifact-v1":
        raise ValueError("unsupported_document_schema")
    value = payload["document"]
    if not isinstance(value, dict) or not isinstance(value.get("sections"), list):
        raise ValueError("invalid_document_root")
    LearningDocument.model_validate({**value, "sections": [], "issues": []})
    return value


def _document_source_refs(document: Mapping[str, Any]) -> list[str]:
    refs: set[str] = set()
    values = [document]
    for section in document["sections"]:
        if isinstance(section, dict) and isinstance(section.get("children"), list):
            values.extend(node for node in section["children"] if isinstance(node, dict))
    for value in values:
        candidates = value.get("source_refs", [])
        if isinstance(candidates, list):
            refs.update(ref for ref in candidates if isinstance(ref, str))
    return sorted(refs)


def _review_from_path(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        payload = _load_json(path)
    except (OSError, ValueError) as exc:
        logger.warning("Cannot read review artifact %s: %s", path, exc)
        return None
    # Preserve malformed issue records for explicit, scoped presentation diagnostics.
    value = payload.get("report") if isinstance(payload, dict) else None
    return value if isinstance(value, dict) else None


_effective_document_status = project_document_status


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "-" for char in value).strip(".-") or "run"


def _mapping_payload(value: Any) -> dict[str, Any] | None:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    return dict(payload) if isinstance(payload, Mapping) else None


def fact_check_summary(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the compact A-003 result for API/index consumers."""

    summary = state.get("fact_check_summary")
    artifact_payload = _mapping_payload(state.get("fact_check_artifact"))
    if summary is None and artifact_payload is not None:
        summary = artifact_payload.get("summary")
    payload = _mapping_payload(summary)
    result = payload or {}
    status = state.get("fact_check_status") or (
        artifact_payload.get("status") if artifact_payload is not None else None
    )
    if status and not result.get("final_status"):
        result["final_status"] = status
    return result or None


def interactive_component_summary(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return only A-004 counters and outcome details to normal API clients."""

    summary = state.get("interactive_component_summary")
    trace = _mapping_payload(state.get("interactive_component_loop_trace"))
    payload = _mapping_payload(summary)
    result = payload or {}
    if not result and trace is not None:
        result = {
            "component_count": len(state.get("interactive_component_artifacts", [])),
            "attempt_count": len(trace.get("attempts") or []),
            "final_status": trace.get("final_status"),
            "stop_reason": trace.get("stop_reason"),
        }
    status = state.get("interactive_component_status") or (trace.get("final_status") if trace is not None else None)
    if status and not result.get("final_status"):
        result["final_status"] = status
    path = state.get("interactive_component_artifact_path")
    if path:
        result["trace_path"] = path
    return result or None


def _artifact_path(state: Mapping[str, Any], kind: str) -> str | None:
    if kind == "source":
        source = state.get("source_document")
        return source.run_metadata.get("json_path") if source is not None else None
    if kind == "blueprint":
        return state.get("blueprint_artifact_path")
    if kind == "content":
        return state.get("content_artifact_path")
    if kind == "document":
        return state.get("document_artifact_path")
    if kind == "quiz":
        return state.get("quiz_artifact_path")
    if kind == "fact_check":
        return state.get("fact_check_artifact_path")
    if kind == "interactive_component":
        return state.get("interactive_component_artifact_path")
    if kind == "review":
        return state.get("review_report_path")
    if kind == "run":
        return state.get("run_manifest_path")
    return None


def _blueprint_loop_summary(state: Mapping[str, Any]) -> dict[str, Any] | None:
    if state.get("blueprint_hierarchy_trace") is not None:
        from annotation.workflow.persistence import _blueprint_loop_summary as hierarchy_summary
        return hierarchy_summary(state)
    trace = state.get("blueprint_loop_trace") or state.get("blueprint_trace")
    if trace is None:
        return None
    payload = trace.model_dump(mode="json") if hasattr(trace, "model_dump") else dict(trace)
    attempts = payload.get("attempts") or []
    return {
        "trace_id": payload.get("trace_id"),
        "attempt_count": len(attempts),
        "max_attempts": payload.get("max_attempts"),
        "final_status": payload.get("final_status"),
        "stop_reason": payload.get("stop_reason"),
        "trace_path": state.get("blueprint_trace_path") or payload.get("artifact_path"),
    }


def _content_loop_summary(state: Mapping[str, Any]) -> dict[str, Any] | None:
    summary = state.get("content_loop_summary")
    if summary is not None:
        value = summary.model_dump(mode="json") if hasattr(summary, "model_dump") else dict(summary)
        if state.get("content_loop_status") and not value.get("final_status"):
            value["final_status"] = state["content_loop_status"]
        if state.get("content_loop_trace_path"):
            value["trace_path"] = state["content_loop_trace_path"]
        return value
    traces = state.get("content_loop_traces") or []
    if not traces:
        return None
    values = [item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item) for item in traces]
    statuses = [str(item.get("final_status") or item.get("status") or "") for item in values]
    return {
        "unit_count": len(values),
        "accepted_count": sum(status == "accepted" for status in statuses),
        "blocked_count": sum(status == "blocked" for status in statuses),
        "failed_count": sum(status == "failed" for status in statuses),
        "attempt_count": sum(len(item.get("attempts") or []) for item in values),
        "final_status": "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "accepted",
        "trace_path": state.get("content_loop_trace_path"),
    }


def _register_state_artifacts(repo: PersistenceRepository, state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    run_id = str(state["run_id"])
    registered: dict[str, dict[str, Any]] = {}
    fact_check_payload = _mapping_payload(state.get("fact_check_artifact"))
    fact_check_compact_summary = fact_check_summary(state)
    interactive_component_trace = _mapping_payload(state.get("interactive_component_loop_trace"))
    interactive_component_compact_summary = interactive_component_summary(state)
    for kind in ("source", "blueprint", "content", "quiz", "fact_check", "interactive_component", "review", "document", "run"):
        raw_path = _artifact_path(state, kind)
        if not raw_path or not Path(raw_path).is_file():
            continue
        version = 1
        existing = repo.list_artifacts(run_id, kind=kind)
        if existing:
            # This is only used when an explicitly supplied run id is retried;
            # new API runs always use a fresh id.
            version = max(int(item["version"]) for item in existing) + 1
        source = state.get("source_document")
        blueprint = state.get("blueprint")
        document = state.get("document")
        report = state.get("review_report")
        original_id = {
            "source": source.artifact_id if source is not None else None,
            "blueprint": blueprint.artifact_id if blueprint is not None else None,
            "document": document.artifact_id if document is not None else None,
            "review": report.report_id if report is not None else None,
            "content": f"content-{run_id}",
            "quiz": f"quiz-{run_id}",
            "fact_check": (
                fact_check_payload.get("artifact_id")
                if fact_check_payload is not None
                else f"fact-check-{run_id}"
            ),
            "interactive_component": (
                interactive_component_trace.get("final_component_artifact_id")
                if interactive_component_trace is not None and interactive_component_trace.get("final_component_artifact_id")
                else f"interactive-components-{run_id}"
            ),
            "run": f"run-{run_id}",
        }[kind]
        artifact_id = f"{kind}-{_safe_id(run_id)}-v{version}"
        loop_summary = _blueprint_loop_summary(state)
        content_loop_summary = _content_loop_summary(state)
        artifact_status = (
            source.status if kind == "source" and source is not None else
            loop_summary.get("final_status") if kind == "blueprint" and loop_summary else
            content_loop_summary.get("final_status") if kind == "content" and content_loop_summary else
            str(
                state.get("fact_check_status")
                or (fact_check_payload or {}).get("status")
                or (fact_check_compact_summary or {}).get("final_status")
                or "accepted"
            ) if kind == "fact_check" else
            str(
                state.get("interactive_component_status")
                or (interactive_component_compact_summary or {}).get("final_status")
                or "accepted"
            ) if kind == "interactive_component" else
            report.status if kind == "review" and report is not None else
            getattr(state.get("quiz_coverage_report"), "status", "accepted") if kind == "quiz" else
            document.status if document is not None and kind == "document" else
            "accepted"
        )
        metadata = {"source_artifact_id": original_id, "path_role": kind}
        if kind == "fact_check":
            metadata["fact_check_summary"] = fact_check_compact_summary
        if kind == "interactive_component":
            metadata["interactive_component_summary"] = interactive_component_compact_summary
        try:
            registered[kind] = repo.register_artifact(
                run_id,
                kind,
                raw_path,
                artifact_id=artifact_id,
                version=version,
                status=str(artifact_status),
                metadata=metadata,
            )
        except Exception:
            # An idempotent retry may already have the same immutable row.
            found = repo.get_artifact(artifact_id)
            if found:
                registered[kind] = found
            else:
                raise
    return registered


def persist_workflow_result(
    repo: PersistenceRepository,
    *,
    book: Mapping[str, Any],
    state: Mapping[str, Any],
    origin: str = "workflow",
    publish_document: bool = True,
) -> dict[str, Any]:
    """Index a completed workflow and optionally publish its document version."""

    run_id = str(state["run_id"])
    document = state.get("document")
    report = state.get("review_report")
    loop_summary = _blueprint_loop_summary(state)
    content_loop_summary = _content_loop_summary(state)
    fact_check_compact_summary = fact_check_summary(state)
    interactive_component_compact_summary = interactive_component_summary(state)
    workflow_status = str(state.get("workflow_status") or "")
    blueprint_loop_status = str((loop_summary or {}).get("final_status") or "")
    content_loop_status = str(state.get("content_loop_status") or (content_loop_summary or {}).get("final_status") or "")
    # A resumed workflow may retain an upstream success marker. No success
    # marker can override a required content stage's failure or block.
    # Component status remains diagnostic and does not gate publication.
    stage_statuses = {
        workflow_status, blueprint_loop_status, content_loop_status,
        str(state.get("fact_check_status") or (fact_check_compact_summary or {}).get("final_status") or ""),
    }
    terminal_status = (
        "failed" if "failed" in stage_statuses
        else "blocked" if "blocked" in stage_statuses
        else "failed" if state.get("errors")
        else "succeeded"
    )
    effective_status = _effective_document_status(
        document.status if document is not None else None,
        report.status if report is not None else None,
    )
    if document is not None:
        # Normalize before registering the document artifact too, so the
        # artifact index and document-version projection cannot disagree.
        document.status = effective_status

    # Backfill inexpensive ingestion measurements for books registered before
    # parsing (for example the bundled seed PDF).
    if book.get("page_count") is None or book.get("block_count") is None:
        page_count = max((block.page_number for block in state.get("source_blocks", [])), default=None)
        enriched = repo.register_book(
            book["stored_path"],
            original_filename=book.get("original_filename"),
            title=book.get("title"),
            page_count=page_count,
            block_count=len(state.get("source_blocks", [])),
        )
        book = enriched

    # Keep the searchable source projection in the same SQLite file as the
    # library records.  The legacy standalone index remains readable for
    # existing debug commands and is migrated on first repository access.
    blocks = list(state.get("source_blocks", []))
    if blocks and repo.database_path and repo.database_path != ":memory:":
        index_source_blocks(repo.database_path, blocks)
    artifacts = _register_state_artifacts(repo, state)

    # A blocked content or Blueprint quality loop, or a technical failure, is a
    # complete run even though it intentionally has no learner document/version
    # to publish.
    if document is None or report is None or terminal_status in {"blocked", "failed"}:
        incomplete = [label for label, value in (("最终文档缺失", document), ("最终审核缺失", report)) if value is None]
        run = repo.update_run(
            run_id,
            status="blocked" if terminal_status == "blocked" else "failed",
            provider=str(state.get("provider_metadata", {}).get("provider", "")),
            model=str(state.get("provider_metadata", {}).get("model", "")),
            config_version=str(state.get("provider_metadata", {}).get("config_version", "")),
            origin=origin,
            error="; ".join([*state.get("errors", []), *incomplete]) or None,
            metadata={
                "prompt_profile": state.get("prompt_profile"),
                "artifact_ids": {kind: item["artifact_id"] for kind, item in artifacts.items()},
                "warnings": list(state.get("warnings", [])),
                "provider_metadata": dict(state.get("provider_metadata", {})),
                "blueprint_loop": loop_summary,
                "content_loop": content_loop_summary,
                "fact_check_summary": fact_check_compact_summary,
                "interactive_component_summary": interactive_component_compact_summary,
            },
        )
        return {"run": run, "document": None, "document_version": None, "artifacts": artifacts}
    if not publish_document:
        # Offline/mock runs are useful for tests and local previews, but must
        # not replace the last real model-generated document in the library.
        run = repo.update_run(
            run_id,
            status="succeeded" if not state.get("errors") else "failed",
            provider=str(state.get("provider_metadata", {}).get("provider", "")),
            model=str(state.get("provider_metadata", {}).get("model", "")),
            config_version=str(state.get("provider_metadata", {}).get("config_version", "")),
            origin=origin,
            error="; ".join(state.get("errors", [])) or None,
            metadata={
                "prompt_profile": state.get("prompt_profile"),
                "artifact_ids": {kind: item["artifact_id"] for kind, item in artifacts.items()},
                "warnings": list(state.get("warnings", [])),
                "provider_metadata": dict(state.get("provider_metadata", {})),
                "blueprint_loop": loop_summary,
                "content_loop": content_loop_summary,
                "fact_check_summary": fact_check_compact_summary,
                "interactive_component_summary": interactive_component_compact_summary,
                "document_published": False,
            },
        )
        return {"run": run, "document": document, "document_version": None, "artifacts": artifacts}
    db_document = repo.create_document(
        str(book["book_id"]),
        document.title,
        document_id=document.document_id,
        metadata={"origin": origin, "blueprint_version": document.blueprint_version},
    )
    existing_versions = repo.list_document_versions(document.document_id)
    requested_version = max((int(item["version"]) for item in existing_versions), default=0) + 1
    db_version = repo.add_document_version(
        document.document_id,
        run_id=run_id,
        version=requested_version,
        document_status=effective_status,
        review_status=report.status if report is not None else None,
        artifact_id=artifacts.get("document", {}).get("artifact_id"),
        metadata={
            "origin": origin,
            "document_artifact_path": state.get("document_artifact_path"),
            "review_artifact_path": state.get("review_report_path"),
            "blueprint_artifact_path": state.get("blueprint_artifact_path"),
            "blueprint_loop": loop_summary,
            "content_loop": content_loop_summary,
            "fact_check_summary": fact_check_compact_summary,
            "fact_check_artifact_path": state.get("fact_check_artifact_path"),
            "interactive_component_summary": interactive_component_compact_summary,
            "interactive_component_artifact_path": state.get("interactive_component_artifact_path"),
        },
    )
    run = repo.update_run(
        run_id,
        status=(
            "blocked"
            if report is not None and report.status == "blocked"
            else "succeeded" if not state.get("errors") else "failed"
        ),
        provider=str(state.get("provider_metadata", {}).get("provider", "")),
        model=str(state.get("provider_metadata", {}).get("model", "")),
        config_version=str(state.get("provider_metadata", {}).get("config_version", "")),
        origin=origin,
        error="; ".join(state.get("errors", [])) or None,
        metadata={
            "prompt_profile": state.get("prompt_profile"),
            "artifact_ids": {kind: item["artifact_id"] for kind, item in artifacts.items()},
            "document_version_id": db_version["document_version_id"],
            "warnings": list(state.get("warnings", [])),
            "provider_metadata": dict(state.get("provider_metadata", {})),
            "blueprint_loop": loop_summary,
            "content_loop": content_loop_summary,
            "fact_check_summary": fact_check_compact_summary,
            "interactive_component_summary": interactive_component_compact_summary,
        },
    )
    return {"run": run, "document": db_document, "document_version": db_version, "artifacts": artifacts}


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def load_document_payload(repo: PersistenceRepository, document_id: str, *, version: int | None = None) -> dict[str, Any] | None:
    """Read a document version and enforce the blocked-document visibility rule."""

    document_row = repo.get_document(document_id)
    if not document_row:
        return None
    if version is None:
        version_row = repo.get_current_document_version(document_id)
    else:
        version_row = next((item for item in repo.list_document_versions(document_id) if int(item["version"]) == version), None)
    if not version_row:
        return None
    artifact = repo.get_artifact(version_row["artifact_id"]) if version_row.get("artifact_id") else None
    try:
        document = _document_from_path(artifact["path"]) if artifact else None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Cannot read document artifact %s: %s", artifact["path"] if artifact else None, exc)
        document = None
    metadata = version_row.get("metadata") or {}
    review = _review_from_path(metadata.get("review_artifact_path"))
    projected_status = _effective_document_status(
        version_row.get("document_status"),
        version_row.get("review_status"),
    )
    if document is not None:
        document["status"] = projected_status
    # An invalid root or a formal release block hides the document. Local
    # defects and damaged review records remain visible to the projection.
    run = repo.get_run(version_row["run_id"]) if version_row.get("run_id") else None
    is_blocked = (
        version_row.get("document_status") == "blocked"
        or version_row.get("review_status") == "blocked"
        or document is None
        or projected_status not in {"accepted", "published"}
        or (review is not None and review.get("status") == "blocked")
        or (run is not None and run.get("status") in {"blocked", "failed"})
    )
    return {
        "status": "blocked" if is_blocked else "ok",
        "document": None if is_blocked else document,
        "source_locations": repo.source_locations(_document_source_refs(document)) if document else {},
        "review_report": review,
        "document_record": document_row,
        "document_version": version_row,
        "run": run,
    }


def load_run_payload(repo: PersistenceRepository, run_id: str) -> dict[str, Any] | None:
    run = repo.get_run(run_id)
    if not run:
        return None
    artifacts = repo.list_artifacts(run_id)
    version_id = (run.get("metadata") or {}).get("document_version_id")
    version = repo.get_document_version(version_id) if version_id else None
    loop_summary = (run.get("metadata") or {}).get("blueprint_loop")
    content_loop_summary = (run.get("metadata") or {}).get("content_loop")
    fact_check_compact_summary = (run.get("metadata") or {}).get("fact_check_summary")
    interactive_component_compact_summary = (run.get("metadata") or {}).get("interactive_component_summary")
    content_row = next((item for item in artifacts if item.get("kind") == "content"), None)
    fact_check_row = next((item for item in artifacts if item.get("kind") == "fact_check"), None)
    interactive_component_row = next((item for item in artifacts if item.get("kind") == "interactive_component"), None)
    result: dict[str, Any] = {
        "status": run["status"],
        "run": run,
        "artifacts": artifacts,
        "blueprint_loop": loop_summary,
        "content_loop": content_loop_summary,
        "fact_check_summary": fact_check_compact_summary,
        "fact_check_artifact_path": fact_check_row.get("path") if fact_check_row else None,
        "interactive_component_summary": interactive_component_compact_summary,
        "interactive_component_artifact_path": interactive_component_row.get("path") if interactive_component_row else None,
        "progress": (run.get("metadata") or {}).get("progress"),
    }
    if version:
        result.update(load_document_payload(repo, version["document_id"], version=int(version["version"])) or {})
    elif run["status"] == "succeeded" and (run.get("metadata") or {}).get("document_published") is False:
        # Offline previews have durable artifacts but deliberately no library version.
        document_row = next((item for item in artifacts if item.get("kind") == "document"), None)
        review_row = next((item for item in artifacts if item.get("kind") == "review"), None)
        try:
            document = _document_from_path(document_row["path"]) if document_row else None
        except (OSError, ValueError, KeyError, TypeError):
            document = None
        review = _review_from_path(review_row["path"] if review_row else None)
        blocked = document is None or document.get("status") == "blocked" or (review is not None and review.get("status") == "blocked")
        result.update({
            "status": "blocked" if blocked else "succeeded",
            "document": None if blocked else document,
            "review_report": review,
            "source_locations": repo.source_locations(_document_source_refs(document)) if document else {},
        })
    return result


__all__ = [
    "load_document_payload",
    "load_run_payload",
    "fact_check_summary",
    "interactive_component_summary",
    "new_run_id",
    "persist_workflow_result",
    "repository",
]
