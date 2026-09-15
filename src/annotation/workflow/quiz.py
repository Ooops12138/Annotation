"""Traceable quiz generation and deterministic quality gates.

Quiz generation is deliberately separate from prose generation.  One call
produces a small quiz set for one knowledge unit, while each item carries the
objective and source references needed by the review and rendering layers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from annotation.domain.artifacts import (
    ContextPack,
    ContentTask,
    KnowledgeUnit,
    LearningBlueprint,
    QuizArtifact,
    QuizDraft,
    QuizQuestion,
    ReviewIssue,
)
from annotation.prompt_loader import load_prompt
from annotation.providers.models import ModelProvider, ProviderError, StructuredGenerationRequest
from annotation.workflow.content import render_context_pack


QUIZ_PROMPT_VERSION = "generate_quiz_artifact:v2"
# There is intentionally no default or minimum quiz size.  The agent decides
# how many evidence-supported items are useful for a unit, including zero.
DEFAULT_QUIZ_COUNT: int | None = None


class QuizCheckResult(BaseModel):
    """Deterministic validation result for one persisted quiz artifact."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "blocked"]
    issues: list[ReviewIssue] = Field(default_factory=list)
    valid_question_ids: list[str] = Field(default_factory=list)


class QuizCoverageEntry(BaseModel):
    """One auditable knowledge-unit row in the quiz coverage matrix."""

    model_config = ConfigDict(extra="forbid")

    knowledge_unit_id: str
    quiz_artifact_id: str
    question_ids: list[str] = Field(default_factory=list)
    question_count: int = Field(default=0, ge=0)
    target_objectives: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    valid_question_count: int = Field(default=0, ge=0)


class QuizCoverageReport(BaseModel):
    """Coverage gate for all units in a blueprint.

    The matrix records the agent's selected question count.  There is no
    question-count lower bound: objective gaps and a missing/empty quiz set are
    warnings, while invalid structure, answers, source mappings, or blocked
    artifacts remain blocking.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "blocked"]
    matrix: list[QuizCoverageEntry] = Field(default_factory=list)
    issues: list[ReviewIssue] = Field(default_factory=list)
    missing_unit_ids: list[str] = Field(default_factory=list)
    objective_gaps: dict[str, list[str]] = Field(default_factory=dict)
    invalid_question_ids: list[str] = Field(default_factory=list)


def _issue(
    issue_id: str,
    *,
    category: Literal["fact", "logic", "formula", "coverage", "source", "transition", "conflict", "uncertainty", "stance"],
    severity: Literal["info", "warning", "blocking"],
    message: str,
    target_id: str | None = None,
    source_refs: Iterable[str] = (),
    suggested_action: str | None = None,
) -> ReviewIssue:
    return ReviewIssue(
        issue_id=issue_id,
        category=category,
        severity=severity,
        layer="structure",
        message=message,
        target_id=target_id,
        source_refs=list(source_refs),
        suggested_action=suggested_action,
    )


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _dedupe_issues(issues: Iterable[ReviewIssue]) -> list[ReviewIssue]:
    result: list[ReviewIssue] = []
    seen: set[str] = set()
    for issue in issues:
        if issue.issue_id not in seen:
            result.append(issue)
            seen.add(issue.issue_id)
    return result


def _valid_source_set(
    *,
    valid_source_refs: Iterable[str] | None,
    context_pack: ContextPack | None,
) -> set[str] | None:
    if valid_source_refs is not None:
        return {str(ref).strip() for ref in valid_source_refs if str(ref).strip()}
    if context_pack is None:
        return None
    refs = [*context_pack.selected_source_refs, *context_pack.source_refs]
    refs.extend(excerpt.source_ref for excerpt in context_pack.excerpts)
    return {str(ref).strip() for ref in refs if str(ref).strip()}


def _context_refs(context_pack: ContextPack) -> list[str]:
    """Return the pack's explicit and excerpt refs in stable first-seen order."""

    return _dedupe([
        *context_pack.selected_source_refs,
        *context_pack.source_refs,
        *(excerpt.source_ref for excerpt in context_pack.excerpts),
    ])


def validate_quiz_artifact(
    artifact: QuizArtifact,
    *,
    unit: KnowledgeUnit | None = None,
    context_pack: ContextPack | None = None,
    valid_source_refs: Iterable[str] | None = None,
    required_question_count: int | None = DEFAULT_QUIZ_COUNT,
) -> QuizCheckResult:
    """Validate structure, evidence and objective mappings without inference.

    The function intentionally returns a blocked result instead of repairing
    model output.  Callers can persist ``artifact.raw_response`` and the
    returned issues before requesting a new version.
    """

    issues: list[ReviewIssue] = []
    valid_ids: list[str] = []
    unit_id = unit.artifact_id if unit else (artifact.knowledge_unit_ids[0] if artifact.knowledge_unit_ids else None)
    objective_set = set(unit.learning_objectives) if unit else set()
    # The chapter source set proves that a ref exists, while the ContextPack
    # set enforces the narrower "use only this call's evidence" boundary.
    source_set = _valid_source_set(valid_source_refs=valid_source_refs, context_pack=None)
    context_source_set = _valid_source_set(valid_source_refs=None, context_pack=context_pack)

    if artifact.content_type != "quiz":
        issues.append(_issue(
            f"quiz-invalid-content-type-{artifact.artifact_id}",
            category="logic",
            severity="blocking",
            message="题目 artifact 的 content_type 必须为 quiz。",
            target_id=artifact.artifact_id,
        ))
    if not artifact.task_id:
        issues.append(_issue(
            f"quiz-missing-task-{artifact.artifact_id}",
            category="coverage",
            severity="blocking",
            message="题目 artifact 缺少 task_id，无法回溯生成任务。",
            target_id=artifact.artifact_id,
        ))
    if not artifact.context_pack_id:
        issues.append(_issue(
            f"quiz-missing-context-pack-{artifact.artifact_id}",
            category="source",
            severity="blocking",
            message="题目 artifact 缺少 context_pack_id，无法确认其证据边界。",
            target_id=artifact.artifact_id,
        ))
    if not artifact.prompt_version:
        issues.append(_issue(
            f"quiz-missing-prompt-version-{artifact.artifact_id}",
            category="logic",
            severity="blocking",
            message="题目 artifact 缺少 prompt_version，无法复现生成契约。",
            target_id=artifact.artifact_id,
        ))
    if len(artifact.knowledge_unit_ids) != 1:
        issues.append(_issue(
            f"quiz-invalid-unit-count-{artifact.artifact_id}",
            category="coverage",
            severity="blocking",
            message="一个 quiz artifact 必须且只能对应一个知识单元。",
            target_id=artifact.artifact_id,
        ))
    if artifact.question_count is not None and artifact.question_count != len(artifact.questions):
        issues.append(_issue(
            f"quiz-question-count-mismatch-{artifact.artifact_id}",
            category="logic",
            severity="blocking",
            message="题目 artifact 记录的 question_count 与实际题目数量不一致。",
            target_id=artifact.artifact_id,
            suggested_action="将 question_count 更新为 questions 数组的实际长度后重新生成 artifact。",
        ))
    if unit_id and artifact.knowledge_unit_ids and artifact.knowledge_unit_ids != [unit_id]:
        issues.append(_issue(
            f"quiz-unit-mismatch-{artifact.artifact_id}",
            category="coverage",
            severity="blocking",
            message="quiz artifact 的知识单元映射与任务单元不一致。",
            target_id=artifact.artifact_id,
        ))
    if not artifact.target_objectives:
        issues.append(_issue(
            f"quiz-missing-objectives-{artifact.artifact_id}",
            category="coverage",
            severity="warning",
            message="题目 artifact 未声明覆盖的学习目标。",
            target_id=artifact.artifact_id,
            suggested_action="补充题目覆盖的蓝图学习目标。",
        ))
    elif objective_set:
        invalid_artifact_objectives = [objective for objective in artifact.target_objectives if objective not in objective_set]
        if invalid_artifact_objectives:
            issues.append(_issue(
                f"quiz-invalid-artifact-objectives-{artifact.artifact_id}",
                category="coverage",
                severity="blocking",
                message=f"题目 artifact 声明了不存在的学习目标：{'、'.join(invalid_artifact_objectives)}。",
                target_id=artifact.artifact_id,
                suggested_action="只引用当前知识单元中稳定的学习目标文本。",
            ))

    # An empty agent-selected quiz has no learner-facing claim that needs a
    # source.  Non-empty sets still require artifact-level evidence.
    if artifact.questions and not artifact.source_refs:
        issues.append(_issue(
            f"quiz-missing-artifact-sources-{artifact.artifact_id}",
            category="source",
            severity="blocking",
            message="题目 artifact 缺少教材来源引用。",
            target_id=artifact.artifact_id,
            suggested_action="为题目和解析补充 ContextPack 中的精确 source_ref。",
        ))
    elif source_set is not None or context_source_set is not None:
        invalid_artifact_refs = [
            ref for ref in artifact.source_refs
            if (source_set is not None and ref not in source_set)
            or (context_source_set is not None and ref not in context_source_set)
        ]
        if invalid_artifact_refs:
            issues.append(_issue(
                f"quiz-invalid-artifact-sources-{artifact.artifact_id}",
                category="source",
                severity="blocking",
                message=f"题目 artifact 包含无法回溯的来源：{'、'.join(invalid_artifact_refs)}。",
                target_id=artifact.artifact_id,
                source_refs=invalid_artifact_refs,
            ))

    if artifact.generation_error:
        issues.append(_issue(
            f"quiz-generation-error-{artifact.artifact_id}",
            category="logic",
            severity="blocking",
            message=f"题目生成失败：{artifact.generation_error}",
            target_id=artifact.artifact_id,
            suggested_action="保留原始响应并重新生成该知识单元的题目 artifact。",
        ))
    if context_pack is not None and context_pack.knowledge_unit_id != unit_id:
        issues.append(_issue(
            f"quiz-context-pack-unit-mismatch-{artifact.artifact_id}",
            category="coverage",
            severity="blocking",
            message="题目 artifact 的 ContextPack 未绑定到当前知识单元。",
            target_id=artifact.artifact_id,
        ))
    if context_pack is not None and artifact.task_id and context_pack.task_id != artifact.task_id:
        issues.append(_issue(
            f"quiz-context-pack-task-mismatch-{artifact.artifact_id}",
            category="coverage",
            severity="blocking",
            message="题目 artifact 的 task_id 与 ContextPack 不一致。",
            target_id=artifact.artifact_id,
        ))

    seen_question_ids: set[str] = set()
    for question in artifact.questions:
        question_issues: list[ReviewIssue] = []
        question_id = question.question_id or "unknown"
        if not question.question_id.strip():
            question_issues.append(_issue(
                f"quiz-empty-question-id-{artifact.artifact_id}-{question_id}",
                category="logic",
                severity="blocking",
                message="题目 question_id 不能为空。",
                target_id=question_id,
            ))
        if not question.knowledge_unit_id.strip():
            question_issues.append(_issue(
                f"quiz-empty-question-unit-{artifact.artifact_id}-{question_id}",
                category="coverage",
                severity="blocking",
                message="题目 knowledge_unit_id 不能为空。",
                target_id=question_id,
            ))
        if question_id in seen_question_ids:
            question_issues.append(_issue(
                f"quiz-duplicate-question-{artifact.artifact_id}-{question_id}",
                category="logic",
                severity="blocking",
                message="题目 question_id 必须唯一。",
                target_id=question_id,
            ))
        seen_question_ids.add(question_id)
        if unit_id and question.knowledge_unit_id != unit_id:
            question_issues.append(_issue(
                f"quiz-question-unit-mismatch-{artifact.artifact_id}-{question_id}",
                category="coverage",
                severity="blocking",
                message="题目映射到了错误的知识单元。",
                target_id=question_id,
            ))
        if not question.target_objectives:
            question_issues.append(_issue(
                f"quiz-question-missing-objective-{artifact.artifact_id}-{question_id}",
                category="coverage",
                severity="warning",
                message="题目未声明覆盖的学习目标。",
                target_id=question_id,
                suggested_action="补充该题对应的蓝图学习目标；目标缺口本轮不单独阻塞发布。",
            ))
        elif objective_set:
            invalid_objectives = [objective for objective in question.target_objectives if objective not in objective_set]
            if invalid_objectives:
                question_issues.append(_issue(
                    f"quiz-invalid-objective-{artifact.artifact_id}-{question_id}",
                    category="coverage",
                    severity="blocking",
                    message=f"题目声明了不存在的学习目标：{'、'.join(invalid_objectives)}。",
                    target_id=question_id,
                    suggested_action="将 target_objectives 限定为该知识单元的学习目标。",
                ))
        options = list(question.options or [])
        if len(options) < 2:
            question_issues.append(_issue(
                f"quiz-too-few-options-{artifact.artifact_id}-{question_id}",
                category="logic",
                severity="blocking",
                message="单选题至少需要两个选项。",
                target_id=question_id,
            ))
        if len(set(options)) != len(options):
            question_issues.append(_issue(
                f"quiz-duplicate-options-{artifact.artifact_id}-{question_id}",
                category="logic",
                severity="blocking",
                message="单选题选项必须互不重复，答案才能唯一。",
                target_id=question_id,
            ))
        if question.answer not in options or options.count(question.answer) != 1:
            question_issues.append(_issue(
                f"quiz-invalid-answer-{artifact.artifact_id}-{question_id}",
                category="logic",
                severity="blocking",
                message="答案必须属于选项且只能对应一个选项。",
                target_id=question_id,
                suggested_action="修订选项或答案后重新生成题目 artifact。",
            ))
        if not question.question.strip():
            question_issues.append(_issue(
                f"quiz-empty-question-{artifact.artifact_id}-{question_id}",
                category="logic",
                severity="blocking",
                message="题干不能为空。",
                target_id=question_id,
            ))
        if not question.explanation.strip():
            question_issues.append(_issue(
                f"quiz-empty-explanation-{artifact.artifact_id}-{question_id}",
                category="logic",
                severity="blocking",
                message="答案解析不能为空。",
                target_id=question_id,
            ))
        if not question.source_refs:
            question_issues.append(_issue(
                f"quiz-question-missing-sources-{artifact.artifact_id}-{question_id}",
                category="source",
                severity="blocking",
                message="题目缺少教材来源引用。",
                target_id=question_id,
                suggested_action="为题干和解析补充 ContextPack 中的精确 source_ref。",
            ))
        elif source_set is not None or context_source_set is not None:
            invalid_refs = [
                ref for ref in question.source_refs
                if (source_set is not None and ref not in source_set)
                or (context_source_set is not None and ref not in context_source_set)
            ]
            if invalid_refs:
                question_issues.append(_issue(
                    f"quiz-question-invalid-sources-{artifact.artifact_id}-{question_id}",
                    category="source",
                    severity="blocking",
                    message=f"题目引用了无法回溯的来源：{'、'.join(invalid_refs)}。",
                    target_id=question_id,
                    source_refs=invalid_refs,
                ))
        issues.extend(question_issues)
        if not any(issue.severity == "blocking" for issue in question_issues):
            valid_ids.append(question_id)

    issues = _dedupe_issues([*artifact.issues, *issues])
    if artifact.status == "blocked" and not any(issue.severity == "blocking" for issue in issues):
        issues.append(_issue(
            f"quiz-artifact-blocked-{artifact.artifact_id}",
            category="logic",
            severity="blocking",
            message="题目 artifact 已标记为 blocked，不能进入发布文档。",
            target_id=artifact.artifact_id,
        ))
    status: Literal["accepted", "blocked"] = "blocked" if any(issue.severity == "blocking" for issue in issues) else "accepted"
    return QuizCheckResult(status=status, issues=issues, valid_question_ids=valid_ids)


def build_quiz_coverage_matrix(
    artifacts: Iterable[QuizArtifact],
) -> dict[str, list[dict[str, Any]]]:
    """Build a deterministic ``knowledge_unit_id -> artifact -> evidence`` map."""

    matrix: dict[str, list[dict[str, Any]]] = {}
    ordered_artifacts = sorted(artifacts, key=lambda item: item.artifact_id)
    for artifact in ordered_artifacts:
        unit_ids = artifact.knowledge_unit_ids or [""]
        question_ids = [question.question_id for question in artifact.questions]
        objectives = _dedupe(
            [*artifact.target_objectives, *(objective for question in artifact.questions for objective in question.target_objectives)]
        )
        refs = _dedupe(
            [*artifact.source_refs, *(ref for question in artifact.questions for ref in question.source_refs)]
        )
        for unit_id in unit_ids:
            matrix.setdefault(unit_id, []).append({
                "knowledge_unit_id": unit_id,
                "quiz_artifact_id": artifact.artifact_id,
                "question_ids": question_ids,
                "question_count": artifact.question_count if artifact.question_count is not None else len(question_ids),
                "target_objectives": objectives,
                "source_refs": refs,
            })
    return matrix


def validate_quiz_coverage(
    artifacts: Iterable[QuizArtifact],
    blueprint: LearningBlueprint | Iterable[KnowledgeUnit],
    *,
    context_packs: Mapping[str, ContextPack] | None = None,
    valid_source_refs: Iterable[str] | None = None,
    required_question_count: int | None = DEFAULT_QUIZ_COUNT,
) -> QuizCoverageReport:
    """Build coverage without imposing a question-count lower bound.

    ``required_question_count`` remains accepted for callers using the old
    signature, but it is deliberately not a hard gate.  The agent's selected
    count is represented by each artifact and matrix row; only malformed or
    untraceable questions block the report.
    """

    units = list(blueprint.knowledge_units if isinstance(blueprint, LearningBlueprint) else blueprint)
    artifacts_list = list(artifacts)
    matrix_dict = build_quiz_coverage_matrix(artifacts_list)
    matrix: list[QuizCoverageEntry] = []
    issues: list[ReviewIssue] = []
    missing_units: list[str] = []
    objective_gaps: dict[str, list[str]] = {}
    invalid_question_ids: list[str] = []
    known_units = {unit.artifact_id: unit for unit in units}
    artifacts_by_id = {artifact.artifact_id: artifact for artifact in artifacts_list}
    seen_question_ids: set[str] = set()
    checks: dict[str, QuizCheckResult] = {}

    for artifact in sorted(artifacts_list, key=lambda item: item.artifact_id):
        unit = known_units.get(artifact.knowledge_unit_ids[0]) if artifact.knowledge_unit_ids else None
        pack = context_packs.get(artifact.context_pack_id) if context_packs and artifact.context_pack_id else None
        check = validate_quiz_artifact(
            artifact,
            unit=unit,
            context_pack=pack,
            valid_source_refs=valid_source_refs,
            required_question_count=required_question_count,
        )
        if context_packs is not None and (not artifact.context_pack_id or pack is None):
            check.issues = _dedupe_issues([
                *check.issues,
                _issue(
                    f"quiz-unknown-context-pack-{artifact.artifact_id}",
                    category="source",
                    severity="blocking",
                    message="题目 artifact 引用了不存在的 ContextPack。",
                    target_id=artifact.artifact_id,
                    suggested_action="使用本次任务实际生成的 context_pack_id 重新生成题目。",
                ),
            ])
            check.status = "blocked"
        checks[artifact.artifact_id] = check
        issues.extend(check.issues)
        for question_id in (question.question_id for question in artifact.questions):
            if question_id in seen_question_ids:
                issues.append(_issue(
                    f"quiz-duplicate-question-global-{question_id}",
                    category="logic",
                    severity="blocking",
                    message="不同 quiz artifact 不能复用同一个 question_id。",
                    target_id=question_id,
                ))
            seen_question_ids.add(question_id)
        invalid_question_ids.extend(
            question.question_id
            for question in artifact.questions
            if question.question_id not in check.valid_question_ids
        )

    for unit in units:
        entries = matrix_dict.get(unit.artifact_id, [])
        if not entries:
            missing_units.append(unit.artifact_id)
            issues.append(_issue(
                f"quiz-missing-unit-{unit.artifact_id}",
                category="coverage",
                severity="warning",
                message=f"知识单元“{unit.title}”没有 quiz artifact；题量由 Agent 自主决定。",
                target_id=unit.artifact_id,
                suggested_action="若该单元需要练习，再生成带教材依据的单选题；不要为凑数补题。",
            ))
            continue
        # Only objectives attached to valid learner-facing questions count as
        # covered.  Artifact-level declarations are retained in the matrix
        # for audit, but cannot claim coverage when the question array is empty
        # or its items failed validation.
        covered_objectives = _dedupe(
            objective
            for entry in entries
            for question in artifacts_by_id[entry["quiz_artifact_id"]].questions
            if question.question_id in checks[entry["quiz_artifact_id"]].valid_question_ids
            for objective in question.target_objectives
        )
        gaps = [objective for objective in unit.learning_objectives if objective not in covered_objectives]
        if gaps:
            objective_gaps[unit.artifact_id] = gaps
            issues.append(_issue(
                f"quiz-objective-gap-{unit.artifact_id}",
                category="coverage",
                severity="warning",
                message=f"知识单元“{unit.title}”仍有学习目标未被题目声明覆盖：{'；'.join(gaps)}。",
                target_id=unit.artifact_id,
                suggested_action="后续补题时覆盖缺口；本轮目标缺口不单独阻塞发布。",
            ))
        for entry in entries:
            artifact_check = checks[entry["quiz_artifact_id"]]
            matrix.append(QuizCoverageEntry(
                **entry,
                valid_question_count=len(artifact_check.valid_question_ids),
            ))

    # Artifacts pointing outside the blueprint are not silently ignored.
    for artifact in artifacts_list:
        unknown_ids = [unit_id for unit_id in artifact.knowledge_unit_ids if unit_id not in known_units]
        if unknown_ids:
            issues.append(_issue(
                f"quiz-unknown-unit-{artifact.artifact_id}",
                category="coverage",
                severity="blocking",
                message=f"题目 artifact 指向不存在的知识单元：{'、'.join(unknown_ids)}。",
                target_id=artifact.artifact_id,
            ))

    issues = _dedupe_issues(issues)
    return QuizCoverageReport(
        status="blocked" if any(issue.severity == "blocking" for issue in issues) else "passed",
        matrix=matrix,
        issues=issues,
        missing_unit_ids=missing_units,
        objective_gaps=objective_gaps,
        invalid_question_ids=_dedupe(invalid_question_ids),
    )


def _unit_context(unit: KnowledgeUnit) -> str:
    return json.dumps({
        "knowledge_unit_id": unit.artifact_id,
        "title": unit.title,
        "kind": unit.kind,
        "learning_objectives": unit.learning_objectives,
        "prerequisites": unit.prerequisites,
        "source_refs": unit.source_refs,
    }, ensure_ascii=False, separators=(",", ":"))


def build_quiz_prompt(task: ContentTask, unit: KnowledgeUnit, context_pack: ContextPack) -> str:
    """Render the bounded prompt for one quiz structured call."""

    return load_prompt(
        "generate_quiz_artifact",
        KNOWLEDGE_UNIT_CONTEXT=_unit_context(unit),
        CONTEXT_PACK=render_context_pack(context_pack),
        TARGET_OBJECTIVES=json.dumps(unit.learning_objectives, ensure_ascii=False),
    )


def _mock_quiz_draft(
    unit: KnowledgeUnit,
    context_pack: ContextPack,
    requested_count: int | None = None,
) -> QuizDraft:
    """Produce a stable offline approximation of an agent-selected count."""

    refs = _context_refs(context_pack)
    evidence = context_pack.excerpts[0].text.strip() if context_pack.excerpts else "教材片段"
    objectives = list(unit.learning_objectives)
    # An explicit task value is a fixture/compatibility hint.  Normal tasks
    # leave it unset, so the mock chooses based on available objectives and
    # evidence instead of enforcing a project-wide minimum.
    count = max(0, requested_count) if requested_count is not None else (len(objectives) if refs else 0)
    target_one = objectives[0] if objectives else f"理解{unit.title}"
    target_two = objectives[1] if len(objectives) > 1 else target_one
    questions: list[QuizQuestion] = []
    for index in range(count):
        target = target_one if index % 2 == 0 else target_two
        options = [
            f"教材片段支持：{evidence[:72]}",
            "该说法没有得到当前教材片段支持",
            "只能依据未提供的外部资料判断",
        ]
        questions.append(QuizQuestion(
            question_id=f"{unit.artifact_id}-quiz-{index + 1:02d}",
            knowledge_unit_id=unit.artifact_id,
            target_objectives=[target],
            question=f"关于“{unit.title}”的目标“{target}”，下列哪项可由当前教材依据支持？",
            options=options,
            answer=options[0],
            explanation=f"当前 ContextPack 的教材片段直接支持该表述；本题对应学习目标“{target}”。",
            source_refs=refs,
        ))
    return QuizDraft(
        knowledge_unit_id=unit.artifact_id,
        question_count=count,
        target_objectives=objectives,
        questions=questions,
    )


def _response_metadata(response: Any) -> dict[str, Any]:
    return {
        "provider": getattr(response, "provider", "unknown"),
        "model": getattr(response, "model", "unknown"),
        "base_url": getattr(response, "base_url", None),
        "config_version": getattr(response, "config_version", "unknown"),
        "duration_ms": getattr(response, "duration_ms", 0),
        "usage": dict(getattr(response, "usage", {}) or {}),
    }


def blocked_quiz_artifact(
    *,
    task: ContentTask,
    unit: KnowledgeUnit,
    context_pack: ContextPack,
    run_id: str | None = None,
    provider_name: str = "unknown",
    raw_response: str = "",
    error: str,
    generation_metadata: Mapping[str, Any] | None = None,
) -> QuizArtifact:
    """Create an immutable-by-convention blocked artifact retaining failure data."""

    artifact = QuizArtifact(
        artifact_id=f"quiz-{task.task_id}",
        run_id=run_id or task.run_id,
        version=1,
        status="blocked",
        source_refs=_context_refs(context_pack),
        created_by=f"provider:{provider_name}",
        knowledge_unit_ids=[unit.artifact_id],
        target_objectives=list(unit.learning_objectives),
        question_count=0,
        task_id=task.task_id,
        context_pack_id=context_pack.context_pack_id,
        prompt_version=QUIZ_PROMPT_VERSION,
        generation_metadata=dict(generation_metadata or {}),
        raw_response=raw_response,
        generation_error=error,
    )
    check = validate_quiz_artifact(
        artifact,
        unit=unit,
        context_pack=context_pack,
    )
    artifact.issues = _dedupe_issues(check.issues)
    return artifact


def generate_quiz_artifact(
    provider: ModelProvider,
    *,
    task: ContentTask,
    unit: KnowledgeUnit,
    context_pack: ContextPack,
    run_id: str | None = None,
) -> QuizArtifact:
    """Generate one quiz set for one unit; mock stays deterministic and local."""

    prompt = build_quiz_prompt(task, unit, context_pack)
    provider_name = str(getattr(provider, "provider", "unknown"))
    metadata: dict[str, Any] = {
        "agent": "generate_quiz_artifact",
        "task_id": task.task_id,
        "context_pack_id": context_pack.context_pack_id,
        "requested_question_count": task.quiz_count,
    }
    raw_response = ""
    try:
        # Built-in mock fixtures intentionally do not consume a structured
        # provider call; this keeps existing Blueprint call-count tests stable.
        if provider_name == "mock":
            draft = _mock_quiz_draft(unit, context_pack, task.quiz_count)
            metadata.update({
                "provider": "mock",
                "model": getattr(provider, "model", "fixture-model"),
                "config_version": getattr(provider, "config_version", "mock-v1"),
                "duration_ms": 0,
                "usage": {},
            })
            raw_response = draft.model_dump_json()
        else:
            response = provider.generate_structured(StructuredGenerationRequest(
                prompt=prompt,
                schema=QuizDraft,
                max_output_tokens=min(2200, getattr(getattr(provider, "capabilities", None), "max_output_tokens", 2200) or 2200),
                metadata=metadata,
            ))
            # Keep the provider payload before validating the typed draft so a
            # later schema/domain failure can still be audited and retried.
            raw_response = str(getattr(response, "raw_text", "") or "")
            draft = QuizDraft.model_validate(response.value.model_dump(mode="python"))
            response_data = _response_metadata(response)
            metadata.update(response_data)
            raw_response = raw_response or draft.model_dump_json()
        selected_count = draft.question_count if draft.question_count is not None else len(draft.questions)
        metadata["question_count"] = selected_count
        metadata["question_count_source"] = "agent_declared" if draft.question_count is not None else "inferred_from_questions"
        artifact = QuizArtifact(
            artifact_id=f"quiz-{task.task_id}",
            run_id=run_id or task.run_id,
            version=1,
            status="draft",
            source_refs=_context_refs(context_pack),
            created_by=f"provider:{metadata.get('provider', provider_name)}",
            knowledge_unit_ids=[unit.artifact_id],
            target_objectives=_dedupe([
                *draft.target_objectives,
                *(objective for question in draft.questions for objective in question.target_objectives),
            ]),
            questions=list(draft.questions),
            question_count=selected_count,
            task_id=task.task_id,
            context_pack_id=context_pack.context_pack_id,
            prompt_version=QUIZ_PROMPT_VERSION,
            generation_metadata={**metadata, "prompt": prompt},
            raw_response=raw_response,
        )
        if draft.knowledge_unit_id != unit.artifact_id:
            artifact.issues.append(_issue(
                f"quiz-draft-unit-mismatch-{artifact.artifact_id}",
                category="coverage",
                severity="blocking",
                message="题目生成响应的 knowledge_unit_id 与当前任务单元不一致。",
                target_id=artifact.artifact_id,
                suggested_action="仅使用当前任务知识单元的稳定 ID 重新生成题目。",
            ))
        check = validate_quiz_artifact(
            artifact,
            unit=unit,
            context_pack=context_pack,
        )
        artifact.issues = check.issues
        artifact.status = "accepted" if check.status == "accepted" else "blocked"
        return artifact
    except ProviderError as exc:
        return blocked_quiz_artifact(
            task=task,
            unit=unit,
            context_pack=context_pack,
            run_id=run_id,
            provider_name=provider_name,
            raw_response=str(getattr(exc, "raw_output", "") or raw_response),
            error=str(exc),
            generation_metadata={**metadata, "error_category": getattr(exc, "category", "provider")},
        )
    except Exception as exc:
        return blocked_quiz_artifact(
            task=task,
            unit=unit,
            context_pack=context_pack,
            run_id=run_id,
            provider_name=provider_name,
            raw_response=raw_response,
            error=str(exc),
            generation_metadata={**metadata, "error_category": "runtime"},
        )


# Compatibility aliases keep the contract discoverable to callers that use
# ``check_*``/``coverage_*`` naming while retaining one implementation.
check_quiz_artifact = validate_quiz_artifact
quiz_coverage_matrix = build_quiz_coverage_matrix
check_quiz_coverage = validate_quiz_coverage
build_coverage_matrix = build_quiz_coverage_matrix
coverage_matrix = build_quiz_coverage_matrix
quiz_coverage = validate_quiz_coverage
