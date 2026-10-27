from __future__ import annotations

import json
import pytest

from annotation.domain.artifacts import (
    ContentArtifact,
    ContentTask,
    ContextExcerpt,
    ContextPack,
    KnowledgeUnit,
)
from annotation.workflow.content_support import _content_hard_check
from annotation.workflow.content_support import _content_artifact_from_draft
from annotation.workflow.models import CalloutDraft, ContentDraft


@pytest.mark.parametrize("command", ["frac{a}{b}", "neq", "text{x}", "beta", "right)"])
@pytest.mark.parametrize("field", ["content", "callout"])
def test_hard_check_rejects_json_decoded_latex_escape_damage(command, field):
    broken = json.loads('{"value":"$' + chr(92) + command + '$"}')["value"]
    draft = ContentDraft(title="title", content=broken if field == "content" else "Valid prose.", source_refs=["src-1"],
        callouts=[CalloutDraft(title="note", content=broken)] if field == "callout" else [])
    artifact = _content_artifact_from_draft(draft=draft, task=_task(), unit=_unit(), pack=_pack(), run_id=_task().run_id, provider_name="test")
    check = _content_hard_check(task=_task(), unit=_unit(), context_pack=_pack(), valid_source_refs={"src-1"}, candidate_artifact=artifact)
    assert check.status == "needs_revision"
    assert any(issue.category == "formula" for issue in check.issues)


def test_valid_json_escaped_math_and_multiline_markdown_are_preserved():
    value = json.loads(r'{"content":"Paragraph.\n\n$\\frac{a}{b} \\neq 0$\n\n$$\\begin{aligned}a&=b\\\\\nc&=d\\end{aligned}$$"}')["content"]
    candidate = _primary(source_refs=["src-1"], content=value)
    result = _content_hard_check(task=_task(), unit=_unit(), context_pack=_pack(), valid_source_refs={"src-1"}, candidate_artifact=candidate)
    assert result.status == "accepted"
    assert candidate.content == value


def test_evidence_notes_persist_and_require_task_scoped_sources(tmp_path, monkeypatch):
    from annotation.workflow.content import write_content_run_artifact
    from annotation.workflow.review_nodes import review
    from annotation.workflow.helpers import assemble_document_from_artifacts
    import annotation.workflow.review_nodes as review_nodes
    task, unit, pack = _task(), _unit(), _pack()
    draft = ContentDraft(title="title", content="Explanation.", source_refs=["src-1"],
        evidence_notes=[{"claim": "uncertain claim", "reason": "ambiguous wording", "source_refs": ["src-1"]}])
    artifact = _content_artifact_from_draft(draft=draft, task=task, unit=unit, pack=pack, run_id=task.run_id, provider_name="test")
    artifact.status = "accepted"
    path = write_content_run_artifact(run_id=task.run_id, blueprint_version=task.blueprint_version, tasks=[task], context_packs=[pack],
        artifacts=[artifact], checks={}, provider_metadata={}, root=tmp_path)
    persisted = json.loads(path.read_text(encoding="utf-8"))["content_artifacts"][0]
    assert persisted["metadata"]["evidence_notes"][0]["reason"] == "ambiguous wording"
    from annotation.domain.artifacts import LearningBlueprint
    blueprint = LearningBlueprint(artifact_id="bp", run_id=task.run_id, version=1, status="accepted", created_by="test", knowledge_units=[unit], title="Book")
    document = assemble_document_from_artifacts(task.run_id, blueprint, [artifact], "test")
    monkeypatch.setattr(review_nodes, "review_report_for", lambda document, state: type("Report", (), {"report_id": "report", "artifact_path": None})())
    state = {"document": document, "blueprint": blueprint, "source_refs": ["src-1"], "content_tasks": [task], "content_artifacts": [artifact], "context_packs": [pack]}
    reviewed = review(None, state)["document"]
    issue = next(issue for issue in reviewed.issues if issue.issue_id.startswith("review-evidence-notes"))
    assert issue.severity == "warning"
    assert "uncertain claim" not in issue.message and "src-1" not in issue.message
    assert "uncertain claim" not in document.model_dump_json()
    artifact.metadata["evidence_notes"][0]["source_refs"] = ["unknown"]
    checked = _content_hard_check(task=task, unit=unit, context_pack=pack, valid_source_refs={"src-1"}, candidate_artifact=artifact)
    assert checked.status == "needs_revision"


def _unit() -> KnowledgeUnit:
    return KnowledgeUnit(
        artifact_id="unit-reflection-check",
        run_id="run-reflection-check",
        version=1,
        status="accepted",
        source_refs=["src-1"],
        created_by="test",
        title="测试概念",
        kind="concept",
        learning_objectives=["理解测试概念"],
    )


def _task() -> ContentTask:
    return ContentTask(
        depth_guidance="standard",
        task_id="task-reflection-check",
        run_id="run-reflection-check",
        blueprint_version="blueprint:v1",
        knowledge_unit_id="unit-reflection-check",
        source_refs=["src-1"],
    )


def _pack() -> ContextPack:
    return ContextPack(
        context_pack_id="ctx-reflection-check",
        run_id="run-reflection-check",
        task_id="task-reflection-check",
        knowledge_unit_id="unit-reflection-check",
        source_refs=["src-1"],
        selected_source_refs=["src-1"],
        excerpts=[ContextExcerpt(source_ref="src-1", page_number=1, block_index=0, text="教材证据")],
        estimated_input_tokens=2,
        input_budget_tokens=100,
    )


def _primary(*, source_refs: list[str], content: str) -> ContentArtifact:
    return ContentArtifact(
        artifact_id="content-primary",
        run_id="run-reflection-check",
        version=1,
        status="draft",
        source_refs=source_refs,
        created_by="provider:test",
        content_type="explanation",
        knowledge_unit_ids=["unit-reflection-check"],
        task_id="task-reflection-check",
        context_pack_id="ctx-reflection-check",
        content=content,
    )


def test_hard_check_preserves_invalid_sources_and_skips_eligible_candidate() -> None:
    task = _task()
    candidate = _primary(source_refs=["unknown-ref"], content="未闭合公式 $x+1 和 i²")

    result = _content_hard_check(
        task=task,
        unit=_unit(),
        context_pack=_pack(),
        valid_source_refs={"src-1"},
        candidate_artifact=candidate,
    )

    assert result.status == "needs_revision"
    assert candidate.source_refs == ["unknown-ref"]
    issue_ids = {issue.issue_id for issue in result.issues}
    assert f"content-reflection-{task.task_id}-invalid-source-{candidate.artifact_id}" in issue_ids
    assert any(issue.category == "formula" for issue in result.issues)


def test_hard_check_accepts_a_bound_explanation_without_named_material_section() -> None:
    task = _task()
    primary = _primary(
        source_refs=["src-1"],
        content="定义写作 $x^2$。\n\n代入一个教材例子并逐步说明。",
    )

    result = _content_hard_check(
        task=task,
        unit=_unit(),
        context_pack=_pack(),
        valid_source_refs={"src-1"},
        candidate_artifact=primary,
    )

    assert result.status == "accepted"
    assert result.checked_artifact_id == primary.artifact_id
    assert result.issues == []


def test_hard_check_blocks_candidate_from_another_run() -> None:
    task = _task()
    candidate = _primary(source_refs=["src-1"], content="定义写作 $x^2$。")
    candidate.run_id = "run-other"

    result = _content_hard_check(
        task=task,
        unit=_unit(),
        context_pack=_pack(),
        valid_source_refs={"src-1"},
        candidate_artifact=candidate,
    )

    assert result.status == "needs_revision"
    assert f"content-reflection-{task.task_id}-run-binding-{candidate.artifact_id}" in {
        issue.issue_id for issue in result.issues
    }


def test_hard_check_blocks_malformed_optional_callout_metadata() -> None:
    task = _task()
    candidate = _primary(
        source_refs=["src-1"],
        content="定义写作 $x^2$。\n\n代入一个教材例子并逐步说明。",
    )
    candidate.metadata["callouts"] = "not-a-list"

    result = _content_hard_check(
        task=task,
        unit=_unit(),
        context_pack=_pack(),
        valid_source_refs={"src-1"},
        candidate_artifact=candidate,
    )

    assert result.status == "needs_revision"
    assert f"content-reflection-{task.task_id}-invalid-callouts-{candidate.artifact_id}" in {
        issue.issue_id for issue in result.issues
    }


def test_artifact_projection_preserves_model_markdown() -> None:
    draft = ContentDraft(
        title="测试概念",
        content=(
            "## 上界、最大元与上确界\n\n"
            "设 $S$ 是集合。（srcdoc-acb412de0faf0abc-p7-b0）\n\n"
            "ContextPack 没有给出完整推导，因此待核实。\n\n"
            "保留这段正常讲解。"
        ),
        callouts=[CalloutDraft(title="提示", tone="info", content="依据（srcdoc-acb412de0faf0abc-p7-b0）。")],
        source_refs=["src-1"],
    )

    artifact = _content_artifact_from_draft(
        draft=draft,
        task=_task(),
        unit=_unit(),
        pack=_pack(),
        run_id="run-reflection-check",
        provider_name="test",
    )

    assert artifact.content == draft.content
    assert artifact.metadata["callouts"][0]["title"] == draft.callouts[0].title
    assert artifact.metadata["callouts"][0]["content"] == draft.callouts[0].content
    assert artifact.source_refs == ["src-1"]
