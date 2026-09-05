from fastapi import APIRouter

from annotation.domain.artifacts import LearningDocument, RunMetadata
from annotation.fixtures.demo import demo_document

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
