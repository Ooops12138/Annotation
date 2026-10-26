"""Workflow stage nodes with explicit configuration and returned state updates."""

from __future__ import annotations

from typing import Any
from annotation.domain.artifacts import LearningDocument, QuizArtifact, ReviewIssue
from annotation.workflow.content_support import merge_messages
from annotation.workflow.helpers import (
    assemble_document_from_artifacts,
    review_report_for,
)
from annotation.workflow.models import WorkflowState
from annotation.workflow.context import WorkflowContext
import logging

logger = logging.getLogger(__name__)


ARTIFACT_LABELS = {
    "content": "内容",
    "quiz": "题目",
    "interactive-component": "交互组件",
}
BLOCKED_ARTIFACT = "{label} artifact 被阻塞，不能进入发布文档。"
INVALID_ARTIFACT_SOURCES = "{label} artifact 包含无法回溯到本次教材导入的来源引用。"
MISSING_CONTENT_TASKS = "有内容任务未产出可接受 artifact：{tasks}"
MISSING_DOCUMENT_SOURCES = "文档缺少教材来源引用。"
INVALID_DOCUMENT_SOURCES = "文档包含无法回溯到本次教材导入的来源引用。"
EMPTY_DOCUMENT = "文档没有可呈现内容。"
GENERATION_WARNING_ACTION = "对涉及的教材片段、公式候选和生成内容进行人工抽样复核。"


def artifact_issues(
    items: list[Any], *, kind: str, valid_refs: set[str]
) -> list[ReviewIssue]:
    issues = []
    for artifact in items:
        if kind != "quiz":
            issues.extend(artifact.issues)
        if artifact.status == "blocked":
            issues.append(
                ReviewIssue(
                    issue_id=f"review-blocked-{kind}-{artifact.artifact_id}",
                    category="coverage",
                    severity="blocking",
                    message=BLOCKED_ARTIFACT.format(label=ARTIFACT_LABELS[kind]),
                    target_id=artifact.artifact_id,
                )
            )
        invalid_refs = [ref for ref in artifact.source_refs if ref not in valid_refs]
        if invalid_refs:
            issues.append(
                ReviewIssue(
                    issue_id=f"review-invalid-{kind}-sources-{artifact.artifact_id}",
                    category="source",
                    severity="blocking",
                    message=INVALID_ARTIFACT_SOURCES.format(
                        label=ARTIFACT_LABELS[kind]
                    ),
                    target_id=artifact.artifact_id,
                    source_refs=invalid_refs,
                )
            )
    return issues


def publishable_quizzes(state: WorkflowState) -> list[QuizArtifact]:
    """Optional failures remain in diagnostics; only checked candidates reach IR."""
    from annotation.workflow.quiz import validate_quiz_artifact

    blueprint = state.get("content_blueprint", state["blueprint"])
    units = {unit.artifact_id: unit for unit in blueprint.knowledge_units}
    packs = {pack.context_pack_id: pack for pack in state.get("context_packs", [])}
    accepted = []
    question_ids: set[str] = set()
    for artifact in state.get("quiz_artifacts", []):
        unit = (
            units.get(artifact.knowledge_unit_ids[0])
            if len(artifact.knowledge_unit_ids) == 1
            else None
        )
        pack = packs.get(artifact.context_pack_id)
        if artifact.status != "accepted" or unit is None or pack is None:
            continue
        check = validate_quiz_artifact(
            artifact,
            unit=unit,
            context_pack=pack,
            valid_source_refs=state.get("source_refs", []),
        )
        ids = [question.question_id for question in artifact.questions]
        if (
            check.status == "blocked"
            or any(issue.severity == "blocking" for issue in artifact.issues)
            or question_ids.intersection(ids)
        ):
            continue
        accepted.append(artifact)
        question_ids.update(ids)
    return accepted


def assemble_document_ir(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    artifacts = [
        artifact
        for artifact in state.get("content_artifacts", [])
        if artifact.status == "accepted"
    ]
    if not artifacts:
        return {
            "workflow_status": "blocked",
            "errors": merge_messages(
                state.get("errors"), ["assemble_document_requires_accepted_artifacts"]
            ),
        }
    document = assemble_document_from_artifacts(
        state["run_id"],
        state.get("content_blueprint", state["blueprint"]),
        artifacts,
        ctx.provider.provider,
        quiz_artifacts=publishable_quizzes(state),
        interactive_component_artifacts=[
            a
            for a in state.get("interactive_component_artifacts", [])
            if a.status == "accepted"
        ],
    )
    if state.get("document_id"):
        document = document.model_copy(update={"document_id": state["document_id"]})
    return {"document": document}


def validate_document_ir(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    try:
        LearningDocument.model_validate(state["document"].model_dump())
        return {}
    except Exception as exc:
        return {
            "workflow_status": "blocked",
            "errors": merge_messages(
                state.get("errors"), [f"document_validation: {exc}"]
            ),
        }


def review(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    document = state["document"]
    issues = [*document.issues, *state.get("fact_check_issues", [])]
    valid_refs = set(state["source_refs"])
    tasks = state.get("content_tasks", [])
    artifacts = state.get("content_artifacts", [])
    accepted_task_ids = {
        artifact.task_id
        for artifact in artifacts
        if artifact.status == "accepted" and artifact.task_id
    }
    missing = [task.task_id for task in tasks if task.task_id not in accepted_task_ids]
    if missing:
        issues.append(
            ReviewIssue(
                issue_id="review-content-task-coverage",
                category="coverage",
                severity="blocking",
                message=MISSING_CONTENT_TASKS.format(tasks=", ".join(missing)),
                target_id=document.document_id,
            )
        )
    issues.extend(artifact_issues(artifacts, kind="content", valid_refs=valid_refs))
    issues.extend(
        artifact_issues(publishable_quizzes(state), kind="quiz", valid_refs=valid_refs)
    )
    issues.extend(
        artifact_issues(
            [
                a
                for a in state.get("interactive_component_artifacts", [])
                if a.status == "accepted"
            ],
            kind="interactive-component",
            valid_refs=valid_refs,
        )
    )
    if not document.source_refs:
        issues.append(
            ReviewIssue(
                issue_id="review-missing-sources",
                category="source",
                severity="blocking",
                message=MISSING_DOCUMENT_SOURCES,
                target_id=document.document_id,
            )
        )
    elif not set(document.source_refs).issubset(valid_refs):
        issues.append(
            ReviewIssue(
                issue_id="review-invalid-sources",
                category="source",
                severity="blocking",
                message=INVALID_DOCUMENT_SOURCES,
                target_id=document.document_id,
            )
        )
    if not document.sections or not any(
        section.children for section in document.sections
    ):
        issues.append(
            ReviewIssue(
                issue_id="review-empty-document",
                category="coverage",
                severity="blocking",
                message=EMPTY_DOCUMENT,
                target_id=document.document_id,
            )
        )
    if state.get("warnings"):
        issues.append(
            ReviewIssue(
                issue_id="review-generation-warning",
                category="uncertainty",
                severity="warning",
                layer="fact",
                message="；".join(state["warnings"]),
                target_id=document.document_id,
                suggested_action=GENERATION_WARNING_ACTION,
            )
        )
    by_id: dict[str, ReviewIssue] = {}
    for issue in issues:
        by_id.setdefault(issue.issue_id, issue)
    deduped = list(by_id.values())
    document = document.model_copy(
        update={
            "issues": deduped,
            "status": "blocked"
            if any(i.severity == "blocking" for i in deduped)
            else "accepted",
        }
    )
    report = review_report_for(document, state)
    document = document.model_copy(update={"review_report_id": report.report_id})
    return {
        "document": document,
        "review_report": report,
        "review_report_path": report.artifact_path or "",
    }


def assemble(ctx: WorkflowContext, state: WorkflowState) -> dict[str, Any]:
    document = state["document"]
    report = state.get("review_report")
    status = document.status
    if report is not None and report.status == "blocked":
        status = "blocked"
    elif (
        not state.get("errors")
        and status == "accepted"
        and report is not None
        and report.status == "passed"
    ):
        status = "published"
    return {"document": document.model_copy(update={"status": status})}
