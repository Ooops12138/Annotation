from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from annotation.config import BOOKS_DIR, SOURCE_INDEX_PATH, STORAGE_DIR, first_book_pdf
from annotation.domain.artifacts import LearningDocument, RunMetadata
from annotation.fixtures.demo import demo_document
from annotation.ingestion.pdf_parser import parse_pdf
from annotation.api.persistence_service import (
    fact_check_summary,
    load_document_payload,
    load_run_payload,
    new_run_id,
    persist_workflow_result,
    repository,
)
from annotation.providers import create_provider_from_env
from annotation.retrieval import index_source_blocks, search_source_blocks
from annotation.workflow import run_minimal_workflow


router = APIRouter()


class RunRequest(BaseModel):
    book_id: str
    run_id: str | None = Field(default=None, min_length=1, max_length=160)


def _safe_run_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "run"


def _read_artifact_payload(path: str | Path | None) -> dict[str, Any] | None:
    if not path or not Path(path).is_file():
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _book_for_run(repo: Any, book_id: str | None = None) -> dict[str, Any]:
    if book_id:
        book = repo.get_book(book_id)
        if not book:
            raise HTTPException(status_code=404, detail=f"未找到教材：{book_id}")
        return book
    try:
        return repo.register_book(first_book_pdf())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _run_response_from_state(result: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    document = state.get("document")
    report = state.get("review_report")
    run = result.get("run") or {}
    loop = state.get("blueprint_loop_trace")
    loop_summary = None
    if loop is not None:
        payload = loop.model_dump(mode="json") if hasattr(loop, "model_dump") else dict(loop)
        loop_summary = {
            "trace_id": payload.get("trace_id"),
            "attempt_count": len(payload.get("attempts") or []),
            "max_attempts": payload.get("max_attempts"),
            "final_status": payload.get("final_status"),
            "stop_reason": payload.get("stop_reason"),
            "trace_path": state.get("blueprint_trace_path") or payload.get("artifact_path"),
        }
    content_loop_summary = state.get("content_loop_summary")
    if content_loop_summary is not None:
        content_loop_summary = content_loop_summary.model_dump(mode="json") if hasattr(content_loop_summary, "model_dump") else dict(content_loop_summary)
        if state.get("content_loop_status") and not content_loop_summary.get("final_status"):
            content_loop_summary["final_status"] = state["content_loop_status"]
        if state.get("content_loop_trace_path"):
            content_loop_summary["trace_path"] = state["content_loop_trace_path"]
    elif state.get("content_loop_traces"):
        traces = [item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item) for item in state["content_loop_traces"]]
        statuses = [str(item.get("final_status") or item.get("status") or "") for item in traces]
        content_loop_summary = {
            "unit_count": len(traces),
            "accepted_count": sum(status == "accepted" for status in statuses),
            "blocked_count": sum(status == "blocked" for status in statuses),
            "failed_count": sum(status == "failed" for status in statuses),
            "attempt_count": sum(len(item.get("attempts") or []) for item in traces),
            "final_status": "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "accepted",
            "trace_path": state.get("content_loop_trace_path"),
        }
    fact_check_compact_summary = fact_check_summary(state)
    blocked = (
        state.get("workflow_status") == "blocked"
        or run.get("status") == "blocked"
        or bool(document and document.status == "blocked")
        or bool(report and report.status == "blocked")
    )
    return {
        "status": run.get("status") if run.get("status") in {"blocked", "failed"} else "ok",
        "run": run,
        "document": None if blocked else document.model_dump(mode="json") if document else None,
        "document_id": document.document_id if document else None,
        "document_status": document.status if document else None,
        "review_report": report.model_dump(mode="json") if report else None,
        "review_status": report.status if report else None,
        "quiz_artifacts": [artifact.model_dump(mode="json") for artifact in state.get("quiz_artifacts", [])],
        "quiz_coverage": (
            state.get("quiz_coverage_report").model_dump(mode="json")
            if state.get("quiz_coverage_report") is not None
            and hasattr(state.get("quiz_coverage_report"), "model_dump")
            else state.get("quiz_coverage_report")
        ),
        "quiz_artifact_path": state.get("quiz_artifact_path"),
        "artifacts": result.get("artifacts", {}),
        "errors": list(state.get("errors", [])),
        "warnings": list(state.get("warnings", [])),
        "blueprint_loop": loop_summary,
        "content_loop": content_loop_summary,
        "fact_check_summary": fact_check_compact_summary,
        "fact_check_artifact_path": state.get("fact_check_artifact_path"),
    }


def _stored_compatibility_payload(repo: Any, run_id: str) -> dict[str, Any] | None:
    """Recreate the original workflow endpoint response without model calls."""

    loaded = load_run_payload(repo, run_id)
    if not loaded:
        return None
    run = loaded.get("run") or {}
    version = loaded.get("document_version") or {}
    metadata = version.get("metadata") or {}
    artifacts = {item.get("kind"): item for item in loaded.get("artifacts", [])}
    content_payload = _read_artifact_payload((artifacts.get("content") or {}).get("path")) or {}
    quiz_payload = _read_artifact_payload((artifacts.get("quiz") or {}).get("path")) or {}
    fact_check_row = artifacts.get("fact_check") or {}
    blueprint_payload = _read_artifact_payload((artifacts.get("blueprint") or {}).get("path")) or {}
    source_payload = _read_artifact_payload((artifacts.get("source") or {}).get("path")) or {}
    document = loaded.get("document")
    report = loaded.get("review_report")
    blocked = loaded.get("status") == "blocked" or not document
    return {
        "status": run.get("status") if run.get("status") in {"blocked", "failed"} else "ok",
        "document": None if blocked else document,
        "review_report": report,
        "review_report_path": metadata.get("review_artifact_path") or (artifacts.get("review") or {}).get("path"),
        "blueprint": blueprint_payload.get("blueprint"),
        "blueprint_check": blueprint_payload.get("blueprint_check"),
        "content_tasks": content_payload.get("tasks", []),
        "context_packs": content_payload.get("context_packs", []),
        "content_artifacts": content_payload.get("content_artifacts", []),
        "quiz_artifacts": content_payload.get("quiz_artifacts") or quiz_payload.get("quiz_artifacts", []),
        "quiz_coverage": content_payload.get("quiz_coverage") or quiz_payload.get("quiz_coverage"),
        "content_artifact_checks": content_payload.get("checks", {}),
        "content_artifact_path": (artifacts.get("content") or {}).get("path"),
        "quiz_artifact_path": (artifacts.get("quiz") or {}).get("path"),
        "fact_check_summary": loaded.get("fact_check_summary"),
        "fact_check_artifact_path": fact_check_row.get("path"),
        "source_document": source_payload.get("document"),
        "source_block_count": len(source_payload.get("blocks", [])),
        "provider_metadata": run.get("metadata", {}).get("provider_metadata") or {
            "provider": run.get("provider", ""),
            "model": run.get("model", ""),
            "config_version": run.get("config_version", ""),
        },
        "errors": [run.get("error")] if run.get("error") else [],
        "warnings": run.get("metadata", {}).get("warnings", []),
        "blueprint_loop": loaded.get("blueprint_loop"),
        "content_loop": loaded.get("content_loop"),
    }


def _execute_run(*, book: dict[str, Any], requested_run_id: str | None = None, origin: str = "workflow") -> dict[str, Any]:
    repo = repository()
    try:
        run_id = requested_run_id or new_run_id()
        existing = repo.get_run(run_id)
        if existing and existing.get("status") == "succeeded":
            loaded = load_run_payload(repo, run_id)
            if loaded:
                return {"stored": True, "payload": loaded}
        if existing and existing.get("status") == "running":
            raise HTTPException(status_code=409, detail=f"运行正在进行：{run_id}")
        if existing and existing.get("status") in {"failed", "blocked"}:
            run_id = new_run_id(_safe_run_id(run_id))

        repo.create_run(book["book_id"], run_id=run_id, origin=origin)
        try:
            provider = create_provider_from_env()
            repo.update_run(
                run_id,
                provider=provider.provider,
                model=provider.model,
                config_version=provider.config_version,
            )
        except Exception as exc:
            repo.update_run(run_id, status="failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"模型配置失败：{exc}") from exc

        try:
            existing_documents = repo.list_documents(book["book_id"])
            logical_document_id = (
                existing_documents[0]["document_id"]
                if existing_documents
                else f"doc-{book['book_id']}"
            )
            state = run_minimal_workflow(
                provider=provider,
                run_id=run_id,
                pdf_path=book["stored_path"],
                document_id=logical_document_id,
            )
            result = persist_workflow_result(repo, book=book, state=state, origin=origin)
            return {"stored": False, "state": state, "result": result}
        except HTTPException:
            raise
        except Exception as exc:
            repo.update_run(run_id, status="failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"工作流运行失败：{exc}") from exc
    finally:
        repo.close()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "annotation-api", "version": "0.1.0"}


@router.get("/api/demo-document", response_model=LearningDocument)
def get_demo_document() -> LearningDocument:
    return demo_document()


@router.get("/api/run-metadata", response_model=RunMetadata)
def get_run_metadata() -> RunMetadata:
    try:
        provider = create_provider_from_env()
        provider_name, model, config_version = provider.provider, provider.model, provider.config_version
    except Exception:
        provider_name, model, config_version = "unknown", "unknown", "invalid-config"
    return RunMetadata(
        run_id="run-demo-001",
        status="configured",
        provider=provider_name,
        model=model,
        config_version=config_version,
    )


@router.get("/api/library")
def get_library() -> dict[str, Any]:
    repo = repository()
    try:
        books = repo.list_library()
        documents = [document | {"book_id": book["book_id"], "book_title": book.get("title")} for book in books for document in book.get("documents", [])]
        return {"status": "ok", "books": books, "documents": documents}
    finally:
        repo.close()


@router.post("/api/books")
async def upload_book(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = Path(file.filename or "textbook.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持 PDF 教材")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    digest = hashlib.sha256(data).hexdigest()
    upload_dir = STORAGE_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    temporary = upload_dir / f".{digest}-{new_run_id('upload')}.pdf"
    temporary.write_bytes(data)
    try:
        try:
            with fitz.open(str(temporary)) as parsed:
                page_count = int(parsed.page_count)
                pdf_title = (parsed.metadata or {}).get("title") or Path(filename).stem
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"无法读取 PDF：{exc}") from exc
        repo = repository()
        try:
            reused = repo.get_book_by_sha256(digest) is not None
            book = repo.register_book(
                temporary,
                original_filename=filename,
                title=pdf_title,
                page_count=page_count,
            )
            return {"status": "ok", "book": book, "reused": reused}
        finally:
            repo.close()
    finally:
        temporary.unlink(missing_ok=True)


@router.post("/api/runs")
def create_run(request: RunRequest) -> dict[str, Any]:
    repo = repository()
    try:
        book = _book_for_run(repo, request.book_id)
    finally:
        repo.close()
    execution = _execute_run(book=book, requested_run_id=request.run_id)
    if execution.get("stored"):
        loaded = execution["payload"]
        content_row = next(
            (item for item in loaded.get("artifacts", []) if item.get("kind") == "content"),
            None,
        ) or {}
        content_payload = _read_artifact_payload(content_row.get("path")) or {}
        quiz_row = next(
            (item for item in loaded.get("artifacts", []) if item.get("kind") == "quiz"),
            None,
        ) or {}
        quiz_payload = _read_artifact_payload(quiz_row.get("path")) or {}
        fact_check_row = next(
            (item for item in loaded.get("artifacts", []) if item.get("kind") == "fact_check"),
            None,
        ) or {}
        return {
            "status": "ok" if loaded.get("run", {}).get("status") == "succeeded" else loaded.get("status", "error"),
            "run": loaded.get("run"),
            "document": loaded.get("document"),
            "document_id": loaded.get("document_record", {}).get("document_id"),
            "review_report": loaded.get("review_report"),
            "review_status": loaded.get("review_report", {}).get("status") if loaded.get("review_report") else None,
            "artifacts": {item.get("kind"): item for item in loaded.get("artifacts", [])},
            # Keep a repeated run response equivalent to a fresh run response:
            # quiz artifacts and their coverage report remain available for
            # audit clients without another generation call.
            "quiz_artifacts": content_payload.get("quiz_artifacts") or quiz_payload.get("quiz_artifacts", []),
            "quiz_coverage": content_payload.get("quiz_coverage") or quiz_payload.get("quiz_coverage"),
            "content_artifact_path": content_row.get("path"),
            "quiz_artifact_path": quiz_row.get("path"),
            "fact_check_summary": loaded.get("fact_check_summary"),
            "fact_check_artifact_path": fact_check_row.get("path"),
            "blueprint_loop": loaded.get("blueprint_loop"),
            "content_loop": loaded.get("content_loop"),
        }
    return _run_response_from_state(execution["result"], execution["state"])


@router.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    repo = repository()
    try:
        payload = load_run_payload(repo, run_id)
        if not payload:
            raise HTTPException(status_code=404, detail=f"未找到运行：{run_id}")
        content_row = next(
            (item for item in payload.get("artifacts", []) if item.get("kind") == "content"),
            None,
        ) or {}
        content_payload = _read_artifact_payload(content_row.get("path")) or {}
        quiz_row = next(
            (item for item in payload.get("artifacts", []) if item.get("kind") == "quiz"),
            None,
        ) or {}
        quiz_payload = _read_artifact_payload(quiz_row.get("path")) or {}
        payload["quiz_artifacts"] = content_payload.get("quiz_artifacts") or quiz_payload.get("quiz_artifacts", [])
        payload["quiz_coverage"] = content_payload.get("quiz_coverage") or quiz_payload.get("quiz_coverage")
        payload["quiz_artifact_path"] = quiz_row.get("path")
        return payload
    finally:
        repo.close()


@router.get("/api/runs/{run_id}/fact-check")
def get_run_fact_check(run_id: str) -> dict[str, Any]:
    """Return the full A-003 trace only through its dedicated audit endpoint."""

    repo = repository()
    try:
        loaded = load_run_payload(repo, run_id)
        if not loaded:
            raise HTTPException(status_code=404, detail=f"未找到运行：{run_id}")
        row = next(
            (item for item in loaded.get("artifacts", []) if item.get("kind") == "fact_check"),
            None,
        )
        if not row:
            raise HTTPException(status_code=404, detail=f"该运行没有事实核查记录：{run_id}")
        artifact_payload = _read_artifact_payload(row.get("path"))
        if artifact_payload is None:
            raise HTTPException(status_code=404, detail=f"事实核查工件不可读取：{run_id}")
        return {
            "status": "ok",
            "run_id": run_id,
            "summary": loaded.get("fact_check_summary") or artifact_payload.get("summary"),
            "fact_check": artifact_payload.get("fact_check", artifact_payload),
            "artifact_path": row.get("path"),
        }
    finally:
        repo.close()


@router.get("/api/documents/{document_id}")
def get_document(document_id: str, version: int | None = None) -> dict[str, Any]:
    repo = repository()
    try:
        payload = load_document_payload(repo, document_id, version=version)
        if not payload:
            raise HTTPException(status_code=404, detail=f"未找到文档：{document_id}")
        return payload
    finally:
        repo.close()


@router.get("/api/documents/{document_id}/versions/{version}")
def get_document_version(document_id: str, version: int) -> dict[str, Any]:
    return get_document(document_id, version=version)


@router.get("/api/source-preview")
def get_source_preview() -> dict[str, object]:
    """Parse the first PDF in books/ for local demo verification."""
    pdfs = sorted(BOOKS_DIR.glob("*.pdf"))
    if not pdfs:
        return {"status": "missing", "message": f"books/ 中没有教材 PDF（查找位置：{BOOKS_DIR}）"}
    document, blocks = parse_pdf(pdfs[0])
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    indexed_count = index_source_blocks(str(SOURCE_INDEX_PATH), blocks)
    return {
        "status": "ok",
        "document": document.model_dump(mode="json"),
        "block_count": len(blocks),
        "indexed_count": indexed_count,
        "warnings": document.warnings,
        "run_metadata": document.run_metadata,
        "artifact_paths": {
            "json": document.run_metadata.get("json_path"),
            "markdown": document.run_metadata.get("markdown_path"),
        },
        "sample_blocks": [block.model_dump(mode="json") for block in blocks[:5]],
    }


@router.get("/api/source-search")
def source_search(query: str, limit: int = 10) -> dict[str, object]:
    """Search the local FTS5 source index and retain exact source references."""
    pdfs = sorted(BOOKS_DIR.glob("*.pdf"))
    if not pdfs:
        return {"status": "missing", "results": []}
    document, blocks = parse_pdf(pdfs[0])
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    index_source_blocks(str(SOURCE_INDEX_PATH), blocks)
    results = search_source_blocks(str(SOURCE_INDEX_PATH), query, limit=limit)
    return {
        "status": "ok",
        "query": query,
        "document_id": document.artifact_id,
        "results": [result.__dict__ for result in results],
    }


@router.post("/api/workflow/run")
def run_workflow(run_id: str = "run-demo-001") -> dict[str, object]:
    """Compatibility endpoint; successful run ids are served from SQLite."""
    repo = repository()
    try:
        stored = _stored_compatibility_payload(repo, run_id)
        if stored:
            return stored
        book = _book_for_run(repo)
    finally:
        repo.close()
    try:
        execution = _execute_run(book=book, requested_run_id=run_id)
        if execution.get("stored"):
            repo = repository()
            try:
                payload = _stored_compatibility_payload(repo, run_id)
                return payload or {"status": "error", "errors": ["无法读取已保存运行"]}
            finally:
                repo.close()
        state = execution["state"]
        result = execution["result"]
        response = _run_response_from_state(result, state)
        # Keep all fields consumed by the original tests and CLI callers.
        response.update({
            "review_report_path": state.get("review_report_path"),
            "blueprint": state.get("blueprint").model_dump(mode="json") if state.get("blueprint") else None,
            "blueprint_check": state.get("blueprint_check").model_dump(mode="json") if state.get("blueprint_check") else None,
            "content_tasks": [task.model_dump(mode="json") for task in state.get("content_tasks", [])],
            "context_packs": [pack.model_dump(mode="json") for pack in state.get("context_packs", [])],
            "content_artifacts": [artifact.model_dump(mode="json") for artifact in state.get("content_artifacts", [])],
            "quiz_artifacts": [artifact.model_dump(mode="json") for artifact in state.get("quiz_artifacts", [])],
            "quiz_coverage": (
                state.get("quiz_coverage_report").model_dump(mode="json")
                if state.get("quiz_coverage_report") is not None
                and hasattr(state.get("quiz_coverage_report"), "model_dump")
                else state.get("quiz_coverage_report")
            ),
            "content_artifact_checks": state.get("content_artifact_checks", {}),
            "content_artifact_path": state.get("content_artifact_path"),
            "quiz_artifact_path": state.get("quiz_artifact_path"),
            "fact_check_summary": fact_check_summary(state),
            "fact_check_artifact_path": state.get("fact_check_artifact_path"),
            "source_document": state.get("source_document").model_dump(mode="json") if state.get("source_document") else None,
            "source_block_count": len(state.get("source_blocks", [])),
            "provider_metadata": state.get("provider_metadata", {}),
            # _run_response_from_state already exposes the compact summary;
            # full trace data remains available only through artifact_path.
            "blueprint_loop": response.get("blueprint_loop"),
        })
        return response
    except HTTPException as exc:
        return {"status": "error", "document": None, "review_report": None, "review_report_path": None, "blueprint": None, "provider_metadata": {}, "errors": [str(exc.detail)]}
    except Exception as exc:
        return {"status": "error", "document": None, "review_report": None, "review_report_path": None, "blueprint": None, "provider_metadata": {}, "errors": [str(exc)]}
