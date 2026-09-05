from fastapi import APIRouter

from annotation.domain.artifacts import LearningDocument, RunMetadata
from annotation.config import BOOKS_DIR
from annotation.fixtures.demo import demo_document
from annotation.ingestion.pdf_parser import parse_pdf
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
    return RunMetadata(
        run_id="run-demo-001",
        status="fixture",
        provider="mock",
        model="fixture-model",
        config_version="t000",
    )


@router.get("/api/source-preview")
def get_source_preview() -> dict[str, object]:
    """Parse the first PDF in books/ for local demo verification."""
    pdfs = sorted(BOOKS_DIR.glob("*.pdf"))
    if not pdfs:
        return {"status": "missing", "message": f"books/ 中没有教材 PDF（查找位置：{BOOKS_DIR}）"}
    document, blocks = parse_pdf(pdfs[0])
    return {
        "status": "ok",
        "document": document.model_dump(mode="json"),
        "block_count": len(blocks),
        "sample_blocks": [block.model_dump(mode="json") for block in blocks[:5]],
    }


@router.post("/api/workflow/run")
def run_workflow() -> dict[str, object]:
    """Run the PDF-driven LangGraph flow using the configured provider."""
    try:
        state = run_minimal_workflow(provider=create_provider_from_env())
    except Exception as exc:
        return {"status": "error", "document": None, "blueprint": None, "provider_metadata": {}, "errors": [str(exc)]}
    return {
        "status": "ok" if not state.get("errors") else "error",
        "document": state.get("document").model_dump(mode="json") if state.get("document") else None,
        "blueprint": state.get("blueprint").model_dump(mode="json") if state.get("blueprint") else None,
        "source_document": state.get("source_document").model_dump(mode="json") if state.get("source_document") else None,
        "source_block_count": len(state.get("source_blocks", [])),
        "provider_metadata": state.get("provider_metadata", {}),
        "errors": state.get("errors", []),
        "warnings": state.get("warnings", []),
    }
