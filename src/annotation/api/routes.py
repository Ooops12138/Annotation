from fastapi import APIRouter

from annotation.domain.artifacts import LearningDocument, RunMetadata
from annotation.config import BOOKS_DIR, SOURCE_INDEX_PATH, STORAGE_DIR
from annotation.fixtures.demo import demo_document
from annotation.ingestion.pdf_parser import parse_pdf
from annotation.retrieval import index_source_blocks, search_source_blocks
from annotation.providers import create_provider_from_env
from annotation.workflow import run_minimal_workflow
from pathlib import Path

router = APIRouter()


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
    """Run the PDF-driven LangGraph flow using the configured provider."""
    try:
        state = run_minimal_workflow(provider=create_provider_from_env(), run_id=run_id)
    except Exception as exc:
        return {"status": "error", "document": None, "review_report": None, "review_report_path": None, "blueprint": None, "provider_metadata": {}, "errors": [str(exc)]}
    return {
        "status": "ok" if not state.get("errors") else "error",
        "document": state.get("document").model_dump(mode="json") if state.get("document") else None,
        "review_report": state.get("review_report").model_dump(mode="json") if state.get("review_report") else None,
        "review_report_path": state.get("review_report_path"),
        "blueprint": state.get("blueprint").model_dump(mode="json") if state.get("blueprint") else None,
        "blueprint_check": state.get("blueprint_check").model_dump(mode="json") if state.get("blueprint_check") else None,
        "content_tasks": [task.model_dump(mode="json") for task in state.get("content_tasks", [])],
        "context_packs": [pack.model_dump(mode="json") for pack in state.get("context_packs", [])],
        "content_artifacts": [artifact.model_dump(mode="json") for artifact in state.get("content_artifacts", [])],
        "content_artifact_checks": state.get("content_artifact_checks", {}),
        "content_artifact_path": state.get("content_artifact_path"),
        "source_document": state.get("source_document").model_dump(mode="json") if state.get("source_document") else None,
        "source_block_count": len(state.get("source_blocks", [])),
        "provider_metadata": state.get("provider_metadata", {}),
        "errors": state.get("errors", []),
        "warnings": state.get("warnings", []),
    }
