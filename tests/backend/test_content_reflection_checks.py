from __future__ import annotations

from annotation.domain.artifacts import (
    ContentArtifact,
    ContentTask,
    ContextExcerpt,
    ContextPack,
    KnowledgeUnit,
)
from annotation.workflow.graph import _content_hard_check


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
