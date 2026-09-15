"""Content artifact construction and deterministic checks."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal

from annotation.domain.artifacts import (
    ContentArtifact,
    ContentCritiqueDraft,
    ContentHardCheckResult,
    ContentTask,
    ContentUnitLoopTrace,
    ContextPack,
    KnowledgeUnit,
    LearningBlueprint,
    ReviewIssue,
)
from annotation.workflow.models import CalloutDraft, ContentDraft

def _formula_format_issues(nodes: list[Any]) -> list[str]:
    """Check Markdown math in learner-facing Markdown and callouts only."""
    issues: list[str] = []
    for node in nodes:
        node_id = getattr(node, "id", "unknown")
        if getattr(node, "type", None) not in {"markdown", "callout"}:
            continue
        content = str(getattr(node, "content", "") or "")
        issues.extend(_markdown_formula_format_issues(node_id, content))
    return issues


def _markdown_formula_format_issues(node_id: str, content: str) -> list[str]:
    """Validate explicit Markdown math delimiters without guessing bare math."""
    issues: list[str] = []
    # Code snippets may legitimately contain dollar signs or Unicode glyphs;
    # they are not learner-facing formulas and must not create review noise.
    prose = re.sub(r"\x60\x60\x60[\s\S]*?\x60\x60\x60|\x60[^\x60\n]*\x60", "", content)
    if _unescaped_token_count(prose, "$$") % 2:
        issues.append(f"正文节点“{node_id}”的 $$ 独立公式定界符未配对。")
    if _unescaped_token_count(prose, r"\(") != _unescaped_token_count(prose, r"\)"):
        issues.append(f"正文节点“{node_id}”的 \\( \\) 行内公式定界符未配对。")
    if _unescaped_token_count(prose, r"\[") != _unescaped_token_count(prose, r"\]"):
        issues.append(f"正文节点“{node_id}”的 \\[ \\] 独立公式定界符未配对。")

    # Count single-dollar delimiters after removing escaped dollars and $$.
    single_dollar_count = 0
    index = 0
    while index < len(prose):
        if prose[index] == "\\":
            index += 2
            continue
        if prose.startswith("$$", index):
            index += 2
            continue
        if prose[index] == "$":
            single_dollar_count += 1
        index += 1
    if single_dollar_count % 2:
        issues.append(f"正文节点“{node_id}”的 $ 行内公式定界符未配对。")
    if re.search(r"[⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉]", prose):
        issues.append(f"正文节点“{node_id}”包含可能未转换的 Unicode 上下标；请将公式明确写成 LaTeX 定界符。")
    return issues


def _unescaped_token_count(value: str, token: str) -> int:
    count = 0
    index = 0
    while index <= len(value) - len(token):
        if value.startswith(token, index):
            slash_count = 0
            cursor = index - 1
            while cursor >= 0 and value[cursor] == "\\":
                slash_count += 1
                cursor -= 1
            if slash_count % 2 == 0:
                count += 1
                index += len(token)
                continue
        index += 1
    return count


def _merge_messages(existing: list[str] | None, new: list[str]) -> list[str]:
    return list(dict.fromkeys([*(existing or []), *new]))


def _normalize_refs(values: list[str], valid_refs: set[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        candidate = value.strip().strip("[]")
        if candidate in valid_refs and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _lossless_refs(values: list[str] | None) -> list[str]:
    """Normalize only whitespace and duplicates while preserving unknown refs."""

    normalized: list[str] = []
    for value in values or []:
        candidate = str(value).strip()
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _plan_content_tasks(run_id: str, blueprint: LearningBlueprint) -> list[ContentTask]:
    tasks: list[ContentTask] = []
    blueprint_version = f"{blueprint.artifact_id}:v{blueprint.version}"
    for index, unit in enumerate(blueprint.knowledge_units, start=1):
        criteria = [
            "覆盖该知识单元的学习目标",
            "只使用 ContextPack 中可定位的教材证据",
            "说明与直接前置知识的必要衔接" if unit.prerequisites else "使用适合初学者的分步解释",
            "由 Agent 根据学习目标和教材证据决定是否生成练习及题量；不为凑数编题",
        ]
        tasks.append(ContentTask(
            task_id=f"task-{run_id}-{index:03d}",
            run_id=run_id,
            blueprint_version=blueprint_version,
            knowledge_unit_id=unit.artifact_id,
            content_types=["explanation", "quiz"],
            source_refs=list(unit.source_refs),
            acceptance_criteria=criteria,
        ))
    return tasks


def _mock_content_draft(unit: KnowledgeUnit, pack: ContextPack) -> ContentDraft:
    refs = pack.source_refs[:3]
    evidence = " ".join(excerpt.text for excerpt in pack.excerpts[:2])
    prerequisite_note = (
        f"本单元建立在“{'、'.join(unit.prerequisites)}”之上。"
        if unit.prerequisites else ""
    )
    return ContentDraft(
        title=unit.title,
        content=(
            "## 讲解\n\n"
            f"本节围绕“{unit.title}”展开。{prerequisite_note}"
            f"学习目标是：{'；'.join(unit.learning_objectives) or '掌握本单元的基本含义和用法'}。"
            f"教材证据摘录：{evidence or '当前没有可用的教材摘录，需要人工审核。'}"
        ),
        source_refs=refs,
        callouts=[],
    )


def _content_artifact_from_draft(
    *,
    draft: ContentDraft,
    task: ContentTask,
    unit: KnowledgeUnit,
    pack: ContextPack,
    run_id: str,
    provider_name: str,
    fixture_adaptation: bool = False,
    attempt: int = 1,
    prompt_version: str = "generate_content_artifact:v1",
) -> ContentArtifact:
    # A real provider's source IDs are evidence.  Preserve unknown IDs so the
    # hard check can report them instead of silently replacing them with the
    # complete ContextPack.  Only the built-in deterministic fixture receives
    # its documented empty-ref adaptation.
    refs = _lossless_refs(draft.source_refs)
    if fixture_adaptation and not refs:
        refs = list(pack.source_refs)
    content = draft.content.strip()
    return ContentArtifact(
        artifact_id=f"content-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        version=attempt,
        status="draft",
        source_refs=refs,
        created_by=f"provider:{provider_name}",
        content_type="explanation",
        knowledge_unit_ids=[unit.artifact_id],
        title=draft.title or unit.title,
        task_id=task.task_id,
        context_pack_id=pack.context_pack_id,
        prompt_version=prompt_version,
        metadata={
            "learning_objectives": list(unit.learning_objectives),
            "omitted_source_refs": list(pack.omitted_source_refs),
            "retrieval_strategy": list(pack.retrieval_strategy),
            "source_snapshot": pack.source_snapshot,
            "callouts": [callout.model_dump(mode="json") for callout in draft.callouts],
            "fixture_adaptation": fixture_adaptation,
        },
        content=content,
    )


def _content_issue(
    *,
    task: ContentTask,
    suffix: str,
    category: Literal["fact", "logic", "formula", "coverage", "source", "transition", "conflict", "uncertainty", "stance"],
    severity: Literal["info", "warning", "blocking"],
    message: str,
    target_id: str | None = None,
    source_refs: list[str] | None = None,
    suggested_action: str | None = None,
) -> ReviewIssue:
    return ReviewIssue(
        issue_id=f"content-reflection-{task.task_id}-{suffix}",
        category=category,
        severity=severity,
        layer="structure",
        message=message,
        target_id=target_id,
        source_refs=list(source_refs or []),
        suggested_action=suggested_action,
    )


def _content_hard_check(
    *,
    task: ContentTask,
    unit: KnowledgeUnit,
    context_pack: ContextPack,
    valid_source_refs: set[str],
    candidate_artifact: ContentArtifact | None,
) -> ContentHardCheckResult:
    """Apply non-negotiable source, binding, and notation checks.

    This intentionally does not judge mathematical truth or pedagogical
    quality.  Those belong to later human review and the constrained Critic.
    """

    issues: list[ReviewIssue] = []
    primary = candidate_artifact
    if primary is None:
        issues.append(_content_issue(
            task=task,
            suffix="missing-explanation",
            category="coverage",
            severity="blocking",
            message="候选缺少主讲解 artifact。",
            suggested_action="重新生成当前知识单元的主讲解。",
        ))
    else:
        if not primary.content.strip():
            issues.append(_content_issue(
                task=task,
                suffix="empty-explanation",
                category="coverage",
                severity="blocking",
                message="主讲解为空，不能进入教学质量评审。",
                target_id=primary.artifact_id,
                suggested_action="补充面向学习者的讲解正文。",
            ))

    pack_refs = set(context_pack.source_refs)
    for artifact in [primary] if primary is not None else []:
        if artifact.run_id != task.run_id:
            issues.append(_content_issue(
                task=task,
                suffix=f"run-binding-{artifact.artifact_id}",
                category="coverage",
                severity="blocking",
                message="候选 artifact 未绑定到当前运行。",
                target_id=artifact.artifact_id,
            ))
        if artifact.task_id != task.task_id:
            issues.append(_content_issue(
                task=task,
                suffix=f"task-binding-{artifact.artifact_id}",
                category="coverage",
                severity="blocking",
                message="候选 artifact 未绑定到当前内容任务。",
                target_id=artifact.artifact_id,
            ))
        if artifact.context_pack_id != context_pack.context_pack_id:
            issues.append(_content_issue(
                task=task,
                suffix=f"context-binding-{artifact.artifact_id}",
                category="source",
                severity="blocking",
                message="候选 artifact 未绑定到当前 ContextPack。",
                target_id=artifact.artifact_id,
            ))
        if artifact.knowledge_unit_ids != [unit.artifact_id]:
            issues.append(_content_issue(
                task=task,
                suffix=f"unit-binding-{artifact.artifact_id}",
                category="coverage",
                severity="blocking",
                message="候选 artifact 的知识单元绑定与当前任务不一致。",
                target_id=artifact.artifact_id,
            ))
        if not artifact.source_refs:
            issues.append(_content_issue(
                task=task,
                suffix=f"missing-source-{artifact.artifact_id}",
                category="source",
                severity="blocking",
                message="候选 artifact 缺少可追溯教材来源。",
                target_id=artifact.artifact_id,
                suggested_action="只使用并返回当前 ContextPack 中的精确 source_ref。",
            ))
        invalid_pack_refs = [ref for ref in artifact.source_refs if ref not in pack_refs]
        invalid_source_refs = [ref for ref in artifact.source_refs if ref not in valid_source_refs]
        if invalid_pack_refs or invalid_source_refs:
            invalid = list(dict.fromkeys([*invalid_pack_refs, *invalid_source_refs]))
            issues.append(_content_issue(
                task=task,
                suffix=f"invalid-source-{artifact.artifact_id}",
                category="source",
                severity="blocking",
                message=f"候选 artifact 包含不属于当前 ContextPack 或本次教材导入的来源：{', '.join(invalid)}。",
                target_id=artifact.artifact_id,
                source_refs=invalid,
                suggested_action="删除未知来源，并使用当前 ContextPack 中的精确 source_ref。",
            ))
        formula_messages = _markdown_formula_format_issues(artifact.artifact_id, artifact.content)
        raw_callouts = artifact.metadata.get("callouts", [])
        if not isinstance(raw_callouts, list):
            issues.append(_content_issue(
                task=task,
                suffix=f"invalid-callouts-{artifact.artifact_id}",
                category="coverage",
                severity="blocking",
                message="候选 artifact 的 callouts 必须是列表。",
                target_id=artifact.artifact_id,
                suggested_action="只返回可选 Callout 对象组成的 callouts 列表。",
            ))
            raw_callouts = []
        for callout_index, raw_callout in enumerate(raw_callouts, start=1):
            try:
                callout = CalloutDraft.model_validate(raw_callout)
            except Exception:
                issues.append(_content_issue(
                    task=task,
                    suffix=f"invalid-callout-{artifact.artifact_id}-{callout_index}",
                    category="coverage",
                    severity="blocking",
                    message="候选 artifact 包含不符合 Callout 契约的特殊内容。",
                    target_id=artifact.artifact_id,
                    suggested_action="只返回带 title、tone 和 content 的可选 callouts。",
                ))
                continue
            formula_messages.extend(_markdown_formula_format_issues(
                f"{artifact.artifact_id}-callout-{callout_index}", callout.content,
            ))
        for index, message in enumerate(formula_messages, start=1):
            issues.append(_content_issue(
                task=task,
                suffix=f"formula-{artifact.artifact_id}-{index}",
                category="formula",
                severity="blocking",
                message=message,
                target_id=artifact.artifact_id,
                suggested_action="使用成对的 Markdown 数学定界符，并将公式直接写在 Markdown 正文或 Callout 内容中。",
            ))

    return ContentHardCheckResult(
        status="accepted" if not issues else "needs_revision",
        issues=issues,
        checked_artifact_id=primary.artifact_id if primary is not None else None,
    )


_CRITIC_BLOCKING_CODES = {
    "objective_missing",
    "prerequisite_unexplained",
}


def _critic_review_issues(
    critique: ContentCritiqueDraft,
    *,
    task: ContentTask,
    primary_artifact: ContentArtifact | None,
    attempt: int,
) -> list[ReviewIssue]:
    category_by_code: dict[str, Literal["logic", "coverage", "transition"]] = {
        "objective_missing": "coverage",
        "prerequisite_unexplained": "transition",
        "beginner_clarity": "logic",
        "organization": "logic",
        "wording": "logic",
    }
    issues: list[ReviewIssue] = []
    for index, finding in enumerate(critique.issues, start=1):
        severity: Literal["warning", "blocking"] = "blocking" if finding.code in _CRITIC_BLOCKING_CODES else "warning"
        issues.append(_content_issue(
            task=task,
            suffix=f"critic-{attempt}-{index}-{finding.code}",
            category=category_by_code[finding.code],
            severity=severity,
            message=finding.message,
            target_id=primary_artifact.artifact_id if primary_artifact is not None else None,
            suggested_action=finding.suggested_action,
        ))
    return issues


def _mock_content_critique() -> ContentCritiqueDraft:
    """Keep the offline fixture deterministic while exercising the Critic stage."""

    return ContentCritiqueDraft(issues=[])


def _blocked_content_artifact(
    *,
    task: ContentTask,
    unit: KnowledgeUnit,
    context_pack: ContextPack | None,
    run_id: str,
    provider_name: str,
    attempt: int,
    error: str,
    issues: list[ReviewIssue] | None = None,
) -> ContentArtifact:
    return ContentArtifact(
        artifact_id=f"content-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        version=max(1, attempt),
        status="blocked",
        source_refs=list(context_pack.source_refs) if context_pack is not None else [],
        created_by=f"provider:{provider_name}",
        content_type="explanation",
        knowledge_unit_ids=[unit.artifact_id],
        title=unit.title,
        task_id=task.task_id,
        context_pack_id=context_pack.context_pack_id if context_pack is not None else None,
        prompt_version="content_reflection:v1",
        metadata={
            "generation_error": error,
            "attempt": attempt,
            "fixture_adaptation": False,
        },
        content="此知识单元未生成可发布讲解。",
        issues=list(issues or []),
    )


def _content_loop_summary(traces: list[ContentUnitLoopTrace]) -> dict[str, Any]:
    """Return a compact, API-safe aggregate; prompts and raw output stay in traces."""

    statuses = [trace.final_status for trace in traces]
    usage: dict[str, int] = {}
    duration_ms = 0
    generation_call_count = 0
    revision_call_count = 0
    critic_call_count = 0
    stop_reasons: dict[str, int] = {}
    unit_summaries: list[dict[str, Any]] = []
    for trace in traces:
        stop_reasons[trace.stop_reason] = stop_reasons.get(trace.stop_reason, 0) + 1
        unit_summaries.append({
            "trace_id": trace.trace_id,
            "task_id": trace.task_id,
            "knowledge_unit_id": trace.knowledge_unit_id,
            "attempt_count": len(trace.attempts),
            "final_status": trace.final_status,
            "stop_reason": trace.stop_reason,
        })
        for item in trace.attempts:
            if item.generation_status != "skipped":
                generation_call_count += 1
                if item.generation_stage == "revision":
                    revision_call_count += 1
            if item.critic_status != "skipped":
                critic_call_count += 1
            for metadata in (item.generation_provider_metadata, item.critic_provider_metadata):
                duration_ms += int(metadata.get("duration_ms") or 0)
                for key, value in dict(metadata.get("usage") or {}).items():
                    try:
                        usage[str(key)] = usage.get(str(key), 0) + int(value)
                    except (TypeError, ValueError):
                        continue
    return {
        "unit_count": len(traces),
        "accepted_count": sum(status == "accepted" for status in statuses),
        "blocked_count": sum(status == "blocked" for status in statuses),
        "failed_count": sum(status == "failed" for status in statuses),
        "skipped_count": sum(status == "skipped" for status in statuses),
        "attempt_count": sum(len(trace.attempts) for trace in traces),
        "generation_call_count": generation_call_count,
        "revision_call_count": revision_call_count,
        "critic_call_count": critic_call_count,
        "duration_ms": duration_ms,
        "usage": usage,
        "stop_reasons": stop_reasons,
        "units": unit_summaries,
        "final_status": "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "accepted",
    }


def _dedupe_review_issues(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    seen: set[str] = set()
    return [issue for issue in issues if not (issue.issue_id in seen or seen.add(issue.issue_id))]


def _unit_context(unit: KnowledgeUnit) -> str:
    return json.dumps(
        {
            "knowledge_unit_id": unit.artifact_id,
            "title": unit.title,
            "kind": unit.kind,
            "learning_objectives": unit.learning_objectives,
            "prerequisites": unit.prerequisites,
            "source_refs": unit.source_refs,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


