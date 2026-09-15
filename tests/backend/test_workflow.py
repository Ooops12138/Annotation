from annotation.providers import MockProvider
from annotation.workflow import run_minimal_workflow
from annotation.workflow.graph import _formula_format_issues, _review_report_for, _source_context
from annotation.domain.artifacts import (
    DocumentSection,
    FormulaNode,
    LearningDocument,
    MarkdownNode,
    QuizNode,
    SourceBlock,
    SourceDocument,
)
from pathlib import Path


def test_source_context_uses_all_non_empty_blocks_by_default() -> None:
    blocks = [
        SourceBlock(
            artifact_id="source-first-artifact",
            source_ref="source-first",
            document_id="source-document",
            run_id="source-run",
            version=1,
            status="draft",
            created_by="test",
            page_number=1,
            block_index=0,
            text="第一段教材内容",
            text_hash="hash-first",
            parser_version="test",
            bbox=(0, 0, 1, 1),
        ),
        SourceBlock(
            artifact_id="source-empty-artifact",
            source_ref="source-empty",
            document_id="source-document",
            run_id="source-run",
            version=1,
            status="draft",
            created_by="test",
            page_number=1,
            block_index=1,
            text="   ",
            text_hash="hash-empty",
            parser_version="test",
            bbox=(0, 0, 1, 1),
        ),
        SourceBlock(
            artifact_id="source-last-artifact",
            source_ref="source-last",
            document_id="source-document",
            run_id="source-run",
            version=1,
            status="draft",
            created_by="test",
            page_number=2,
            block_index=0,
            text="最后一段教材内容",
            text_hash="hash-last",
            parser_version="test",
            bbox=(0, 0, 1, 1),
        ),
    ]

    context = _source_context(blocks)

    assert "[source-first]" in context
    assert "[source-last]" in context
    assert "[source-empty]" not in context
    assert _source_context(blocks, limit=1).count("[") == 1


def test_minimal_langgraph_workflow_produces_traceable_document() -> None:
    state = run_minimal_workflow(provider=MockProvider(), run_id="run-test-001")
    assert not state.get("errors")
    assert state["blueprint"].run_id == "run-test-001"
    assert state["document"].run_id == "run-test-001"
    assert state["document"].status == (
        "published" if state["review_report"].status == "passed" else "accepted"
    )
    assert state["document"].blueprint_version.startswith("bp-fixture-001:v1")
    assert state["provider_metadata"]["provider"] == "mock"
    assert len(state["document"].source_refs) <= 5
    assert not state.get("warnings")
    assert len(state["blueprint"].knowledge_units) >= 4
    assert len(state["blueprint"].knowledge_units) == 6
    assert state["blueprint_check"].status == "accepted"
    assert len(state["document"].sections) >= 3
    assert sum(len(section.children) for section in state["document"].sections) >= 10
    assert len(state["content_tasks"]) == len(state["blueprint"].knowledge_units)
    assert len(state["context_packs"]) == len(state["content_tasks"])
    assert len(state["content_artifacts"]) >= len(state["content_tasks"])
    assert all(artifact.status == "accepted" for artifact in state["content_artifacts"])
    assert all(artifact.context_pack_id for artifact in state["content_artifacts"])
    assert any(artifact.material_role == "bridge" for artifact in state["content_artifacts"])
    assert all(task.status == "accepted" for task in state["content_tasks"])
    assert len(state["quiz_artifacts"]) == 6
    assert all(artifact.question_count == len(artifact.questions) for artifact in state["quiz_artifacts"])
    assert state["quiz_coverage_report"].status == "passed"
    assert sum(getattr(node, "type", "") == "quiz" for section in state["document"].sections for node in section.children) == sum(
        len(artifact.questions) for artifact in state["quiz_artifacts"] if artifact.status == "accepted"
    )
    assert Path(state["quiz_artifact_path"]).is_file()
    assert Path(state["content_artifact_path"]).is_file()
    assert state["review_report"].status in {"passed", "at_risk", "blocked"}
    assert state["review_report"].report_id == state["document"].review_report_id
    assert Path(state["review_report_path"]).is_file()
    assert state["review_report"].checks["formula_format"] == "passed"


def test_workflow_can_use_a_stable_logical_document_id() -> None:
    state = run_minimal_workflow(
        provider=MockProvider(),
        run_id="run-stable-document-id",
        document_id="doc-book-stable",
    )
    assert state["document"].document_id == "doc-book-stable"
    assert state["review_report"].document_id == "doc-book-stable"


def test_formula_format_review_keeps_text_math_and_rejects_malformed_math() -> None:
    issues = _formula_format_issues([
        FormulaNode(id="wrapped", latex="$$x^2$$"),
        FormulaNode(id="unicode", latex="i²=-1"),
        MarkdownNode(id="inline", content="解释公式：$x+1$"),
        MarkdownNode(id="unclosed", content="未完成的公式：$x+1"),
        MarkdownNode(id="unicode-text", content="未转换的公式：i²=-1"),
        MarkdownNode(id="code", content="代码示例：`price $5` 和 `i²`。"),
        MarkdownNode(id="bracketed", content=r"行内公式：\(x+1\)。"),
    ])
    assert any("formula_latex 不应包含" in issue for issue in issues)
    assert any("公式节点“unicode”包含" in issue for issue in issues)
    assert any("正文节点“unclosed”" in issue and "未配对" in issue for issue in issues)
    assert any("正文节点“unicode-text”包含" in issue for issue in issues)
    assert not any("正文节点“inline”" in issue for issue in issues)
    assert not any("正文节点“code”" in issue for issue in issues)
    assert not any("正文节点“bracketed”" in issue for issue in issues)


def test_review_report_version_is_incremented_for_a_repeated_run() -> None:
    first = run_minimal_workflow(provider=MockProvider(), run_id="run-review-version-test")
    second = run_minimal_workflow(provider=MockProvider(), run_id="run-review-version-test")
    assert second["review_report"].version > first["review_report"].version
    assert second["review_report_path"] != first["review_report_path"]
    assert second["review_report"].revision_of == first["review_report"].report_id


def test_review_marks_draft_source_as_unapproved_warning() -> None:
    source = SourceDocument(
        artifact_id="source-document-draft",
        run_id="run-draft-source-review",
        version=1,
        status="draft",
        source_refs=["source-1"],
        created_by="test",
        title="测试教材",
        locator="test://source",
    )
    document = LearningDocument(
        artifact_id="document-draft-source-review",
        document_id="document-draft-source-review",
        run_id=source.run_id,
        version=1,
        status="accepted",
        source_refs=["source-1"],
        created_by="test",
        blueprint_version="blueprint:v1",
        title="测试文档",
        sections=[DocumentSection(
            id="section-1",
            title="第一节",
            children=[
                MarkdownNode(id="markdown-1", content="正文", source_refs=["source-1"]),
                QuizNode(
                    id="quiz-1",
                    question="问题",
                    options=["A", "B"],
                    answer="A",
                    explanation="解释",
                    source_refs=["source-1"],
                ),
            ],
        )],
    )

    report = _review_report_for(document, {
        "source_document": source,
        "source_refs": ["source-1"],
    })

    assert report.checks["source_artifact_status"] == "warning"
    assert any(issue.issue_id == "review-source-artifact-draft" for issue in report.issues)
    assert report.status == "at_risk"
