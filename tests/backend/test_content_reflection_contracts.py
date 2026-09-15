from __future__ import annotations

import pytest
from pydantic import ValidationError

from annotation.domain.artifacts import (
    ContentArtifact,
    ContentAttemptTrace,
    ContentCriticIssueDraft,
    ContentCritiqueDraft,
    ContentHardCheckResult,
    ContentTask,
    ContentUnitLoopTrace,
    KnowledgeUnit,
    ReviewIssue,
)
from annotation.prompt_loader import load_prompt
from annotation.workflow.models import ContentDraft


def _candidate() -> ContentArtifact:
    return ContentArtifact(
        artifact_id="content-candidate-1",
        run_id="run-reflection-contract",
        version=1,
        status="draft",
        source_refs=["src-1"],
        created_by="test",
        content_type="explanation",
        knowledge_unit_ids=["ku-1"],
        task_id="task-1",
        context_pack_id="ctx-1",
        content="解释 $x^2$。",
    )


def _issue() -> ReviewIssue:
    return ReviewIssue(
        issue_id="content-source-1",
        category="source",
        severity="blocking",
        message="来源缺失。",
        target_id="content-candidate-1",
    )


def test_content_reflection_contracts_keep_traceable_candidate_forms() -> None:
    candidate = _candidate()
    hard_check = ContentHardCheckResult(
        status="needs_revision",
        issues=[_issue()],
        checked_artifact_id=candidate.artifact_id,
    )
    critique = ContentCritiqueDraft(issues=[
        ContentCriticIssueDraft(
            code="beginner_clarity",
            message="开头没有解释符号的含义。",
            suggested_action="先定义符号，再给出公式。",
        ),
    ])
    trace = ContentAttemptTrace(
        attempt=1,
        generation_stage="initial",
        generation_prompt="initial prompt",
        candidate_artifact=candidate,
        generation_status="succeeded",
        hard_check=hard_check,
        critic_status="succeeded",
        critic_parsed_output=critique.model_dump(mode="json"),
        route="revise",
    )

    assert trace.candidate_artifact == candidate
    assert trace.hard_check.checked_artifact_id == candidate.artifact_id
    assert ContentAttemptTrace(
        attempt=2,
        candidate_artifact=candidate.model_dump(mode="json"),
        route="block",
    ).candidate_artifact

    unit_trace = ContentUnitLoopTrace(
        trace_id="content-loop-task-1",
        run_id=candidate.run_id,
        task_id="task-1",
        knowledge_unit_id="ku-1",
        context_pack_id="ctx-1",
        max_attempts=3,
        attempts=[trace],
        final_status="blocked",
        final_attempt=1,
        stop_reason="max_attempts",
    )
    assert unit_trace.attempts[0].route == "revise"


def test_content_critic_contract_rejects_unroutable_codes() -> None:
    with pytest.raises(ValidationError):
        ContentCriticIssueDraft(
            code="fact_wrong",
            message="The Critic must not make factual verdicts.",
        )


def test_content_draft_accepts_optional_callouts_but_rejects_legacy_presentation_fields() -> None:
    draft = ContentDraft.model_validate({
        "title": "测试单元",
        "content": "## 讲解\n\n正文。",
        "callouts": [{"title": "定理", "tone": "info", "content": "重要结论。"}],
        "source_refs": ["src-1"],
    })

    assert draft.callouts[0].title == "定理"
    with pytest.raises(ValidationError):
        ContentDraft.model_validate({
            "title": "测试单元",
            "content": "正文。",
            "formula_latex": "x^2",
            "source_refs": ["src-1"],
        })
    with pytest.raises(ValidationError):
        ContentDraft.model_validate({
            "title": "测试单元",
            "content": "正文。",
            "teaching_material": "旧教学材料字段。",
            "source_refs": ["src-1"],
        })


def test_active_content_contract_rejects_retired_material_fields_and_trace_groups() -> None:
    with pytest.raises(ValidationError):
        KnowledgeUnit(
            artifact_id="ku-retired-field",
            run_id="run-retired-field",
            version=1,
            status="draft",
            created_by="test",
            title="测试单元",
            kind="concept",
            teaching_materials=["旧字段"],
        )
    with pytest.raises(ValidationError):
        _candidate().model_validate({
            **_candidate().model_dump(mode="json"),
            "material_role": "example",
        })
    with pytest.raises(ValidationError):
        ContentAttemptTrace(
            attempt=1,
            candidate_artifacts=[_candidate()],
            route="block",
        )
    with pytest.raises(ValidationError):
        ContentTask(
            task_id="task-retired-types",
            run_id="run-retired-types",
            blueprint_version="bp:v1",
            knowledge_unit_id="ku-retired-field",
            content_types=["quiz"],
        )


def test_content_reflection_prompts_render_all_auditable_inputs() -> None:
    values = {
        "KNOWLEDGE_UNIT_CONTEXT": '{"knowledge_unit_id":"ku-1"}',
        "ACCEPTANCE_CRITERIA": '["覆盖学习目标"]',
        "CANDIDATE_ARTIFACT": '{"artifact_id":"content-1"}',
        "CONTEXT_PACK": "[src-1] 教材证据",
        "HARD_CHECK_RESULT": '{"status":"needs_revision"}',
        "CRITIQUE_RESULT": '{"issues":[]}',
    }

    critic = load_prompt(
        "critique_content_artifact",
        **{key: values[key] for key in ("KNOWLEDGE_UNIT_CONTEXT", "ACCEPTANCE_CRITERIA", "CANDIDATE_ARTIFACT")},
    )
    revision = load_prompt("revise_content_artifact", **values)

    assert critic.startswith("# Agent: critique_content_artifact")
    assert "severity" in critic and "route" in critic
    assert "{{CANDIDATE_ARTIFACT}}" not in critic
    assert revision.startswith("# Agent: revise_content_artifact")
    assert "[src-1] 教材证据" in revision
    assert "{{HARD_CHECK_RESULT}}" not in revision
