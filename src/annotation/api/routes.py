from fastapi import APIRouter

from annotation.domain.artifacts import LearningDocument, RunMetadata
from annotation.fixtures.demo import demo_document
from annotation.ingestion.pdf_parser import parse_pdf
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
    books_dir = Path("books")
    pdfs = sorted(books_dir.glob("*.pdf"))
    if not pdfs:
        return {"status": "missing", "message": "books/ 中没有 PDF"}
    document, blocks = parse_pdf(pdfs[0])
    return {
        "status": "ok",
        "document": document.model_dump(mode="json"),
        "block_count": len(blocks),
        "sample_blocks": [block.model_dump(mode="json") for block in blocks[:5]],
    }
