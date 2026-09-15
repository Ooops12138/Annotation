from __future__ import annotations

from annotation.api.persistence_service import load_document_payload, persist_workflow_result
from annotation.domain.artifacts import (
    BlueprintCheckResult,
    ContentArtifact,
    DocumentSection,
    LearningBlueprint,
    LearningDocument,
    MarkdownNode,
    ReviewIssue,
    ReviewReport,
    SourceBlock,
    SourceDocument,
)
from annotation.persistence import PersistenceRepository
from annotation.workflow.persistence import (
    write_blueprint_artifact,
    write_document_artifact,
    write_run_manifest,
)


def _state(tmp_path, *, blocked: bool = False) -> dict:
    run_id = "run-service-test"
    source = SourceDocument(
        artifact_id="srcdoc-service",
        run_id=run_id,
        version=1,
        status="draft",
        source_refs=["src-p1-b0"],
        created_by="test",
        title="测试教材",
        locator="test://book",
        run_metadata={"json_path": str(tmp_path / "source.json")},
    )
    source_path = tmp_path / "source.json"
    source_path.write_text('{"schema_version":"source-artifact-v1","document":{},"blocks":[]}', encoding="utf-8")
    block = SourceBlock(
        artifact_id="src-p1-b0-artifact",
        source_ref="src-p1-b0",
        document_id=source.artifact_id,
        run_id=run_id,
        version=1,
        status="draft",
        source_refs=["src-p1-b0"],
        created_by="test",
        page_number=1,
        block_index=0,
        text="教材证据",
        text_hash="hash",
        parser_version="test",
        bbox=(0, 0, 1, 1),
    )
    unit = LearningBlueprint(
        artifact_id="bp-service",
        run_id=run_id,
        version=1,
        status="accepted",
        source_refs=["src-p1-b0"],
        created_by="test",
        title="测试章节",
        knowledge_units=[],
    )
    document = LearningDocument(
        artifact_id="doc-service-artifact",
        document_id="doc-service",
        run_id=run_id,
        version=1,
        status="blocked" if blocked else "published",
        source_refs=["src-p1-b0"],
        created_by="test",
        blueprint_version="bp-service:v1",
        title="测试文档",
        sections=[DocumentSection(id="section-1", title="一节", children=[MarkdownNode(id="md-1", content="正文", source_refs=["src-p1-b0"])])],
    )
    issue = ReviewIssue(
        issue_id="blocking-1",
        category="source",
        severity="blocking" if blocked else "warning",
        message="需要核查",
        target_id=document.document_id,
    )
    report = ReviewReport(
        artifact_id="review-service-v1",
        report_id="review-service-v1",
        run_id=run_id,
        version=1,
        status="blocked" if blocked else "at_risk",
        created_by="test",
        document_id=document.document_id,
        reviewed_artifact_id=document.artifact_id,
        source_refs=document.source_refs,
        issues=[issue],
        issue_counts={"warning": 0 if blocked else 1, "blocking": 1 if blocked else 0},
        checks={"source_traceability": "blocking" if blocked else "passed"},
    )
    content = ContentArtifact(
        artifact_id="content-service",
        run_id=run_id,
        version=1,
        status="accepted",
        source_refs=["src-p1-b0"],
        created_by="test",
        content_type="explanation",
        content="正文",
    )
    blueprint_path = write_blueprint_artifact({"run_id": run_id, "blueprint": unit, "blueprint_check": BlueprintCheckResult(status="accepted")}, root=tmp_path / "blueprint")
    document_path = write_document_artifact({"run_id": run_id, "document": document, "review_report": report}, root=tmp_path / "document")
    content_path = tmp_path / "content.json"
    content_path.write_text('{"content_artifacts":[]}', encoding="utf-8")
    review_path = tmp_path / "review.json"
    review_path.write_text('{"report":' + report.model_dump_json() + '}', encoding="utf-8")
    state = {
        "run_id": run_id,
        "pdf_path": "test.pdf",
        "source_document": source,
        "source_blocks": [block],
        "source_refs": source.source_refs,
        "blueprint": unit,
        "blueprint_check": BlueprintCheckResult(status="accepted"),
        "content_artifacts": [content],
        "content_artifact_path": str(content_path),
        "document": document,
        "review_report": report,
        "review_report_path": str(review_path),
        "provider_metadata": {"provider": "mock", "model": "fixture"},
        "warnings": [],
        "errors": [],
    }
    state["blueprint_artifact_path"] = str(blueprint_path)
    state["document_artifact_path"] = str(document_path)
    state["run_manifest_path"] = str(write_run_manifest(state, blueprint_path=blueprint_path, document_path=document_path, root=tmp_path / "runs"))
    return state


def test_service_indexes_and_reads_at_risk_document(tmp_path) -> None:
    repo = PersistenceRepository(tmp_path / "db.sqlite3")
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    book = repo.register_book(pdf)
    state = _state(tmp_path)
    repo.create_run(book["book_id"], run_id=state["run_id"])
    persist_workflow_result(repo, book=book, state=state)
    payload = load_document_payload(repo, "doc-service")
    assert payload and payload["status"] == "ok"
    assert payload["document"]["document_id"] == "doc-service"
    # A warning-only review remains readable, but it is an accepted preview;
    # the old published + at_risk pair must not survive persistence.
    assert payload["document"]["status"] == "accepted"
    assert payload["document_version"]["document_status"] == "accepted"
    assert payload["review_report"]["status"] == "at_risk"
    assert repo.get_artifact("source-run-service-test-v1")["status"] == "draft"
    assert repo.get_artifact("document-run-service-test-v1")["status"] == "accepted"
    assert repo.connection.execute("SELECT COUNT(*) FROM source_blocks").fetchone()[0] == 1
    repo.close()


def test_service_hides_blocked_document_but_keeps_review(tmp_path) -> None:
    repo = PersistenceRepository(tmp_path / "db.sqlite3")
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    book = repo.register_book(pdf)
    state = _state(tmp_path, blocked=True)
    repo.create_run(book["book_id"], run_id=state["run_id"])
    persist_workflow_result(repo, book=book, state=state)
    payload = load_document_payload(repo, "doc-service")
    assert payload and payload["status"] == "blocked"
    assert payload["document"] is None
    assert payload["review_report"]["status"] == "blocked"
    repo.close()
