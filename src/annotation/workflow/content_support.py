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
    FormulaNode,
    KnowledgeUnit,
    LearningBlueprint,
    ReviewIssue,
)
from annotation.workflow.models import ContentDraft

def _formula_format_issues(nodes: list[Any]) -> list[str]:
    """Check formula notation contracts, not mathematical correctness.

    Markdown text is intentionally checked only for explicit delimiter errors
    and unmistakable Unicode superscripts/subscripts.  We do not try to infer
    whether arbitrary prose or code is mathematical notation.
    """
    issues: list[str] = []
    for node in nodes:
        node_id = getattr(node, "id", "unknown")
        if getattr(node, "type", None) == "formula":
            latex = str(getattr(node, "latex", "") or "").strip()
            if not latex:
                continue
            if any(delimiter in latex for delimiter in ("$", "\\(", "\\)", "\\[", "\\]")):
                issues.append(f"公式节点“{node_id}”的 formula_latex 不应包含 Markdown 公式定界符。")
            if latex.count("{") != latex.count("}"):
                issues.append(f"公式节点“{node_id}”的 LaTeX 花括号未配对。")
            if re.search(r"[⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉]", latex):
                issues.append(f"公式节点“{node_id}”包含未转换的 Unicode 上下标。")
        else:
            if getattr(node, "type", None) != "markdown":
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
            content_types=["explanation", "teaching_material", "quiz"],
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
            f"本节围绕“{unit.title}”展开。{prerequisite_note}"
            f"学习目标是：{'；'.join(unit.learning_objectives) or '掌握本单元的基本含义和用法'}。"
            f"教材证据摘录：{evidence or '当前没有可用的教材摘录，需要人工审核。'}"
        ),
        material_role="explanation",
        source_refs=refs,
        # Fixture adaptation is explicit at the call site.  The local fixture
        # still supplies a follow-along material so the same group contract is
        # exercised without asking a network provider to invent it.
        teaching_material=_focused_teaching_material(unit, pack) if unit.teaching_materials else None,
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
    role = draft.material_role if draft.material_role in {"explanation", "example", "proof", "bridge", "supplement"} else "explanation"
    content = draft.content.strip()
    return ContentArtifact(
        artifact_id=f"content-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        version=attempt,
        status="draft",
        source_refs=refs,
        created_by=f"provider:{provider_name}",
        content_type="explanation" if role in {"explanation", "bridge", "proof", "supplement"} else "example",
        knowledge_unit_ids=[unit.artifact_id],
        title=draft.title or unit.title,
        material_role=role,
        task_id=task.task_id,
        context_pack_id=pack.context_pack_id,
        prompt_version=prompt_version,
        metadata={
            "learning_objectives": list(unit.learning_objectives),
            "teaching_materials": list(unit.teaching_materials),
            "formula_latex": draft.formula_latex,
            "omitted_source_refs": list(pack.omitted_source_refs),
            "retrieval_strategy": list(pack.retrieval_strategy),
            "source_snapshot": pack.source_snapshot,
            "candidate_teaching_material": draft.teaching_material,
            "fixture_adaptation": fixture_adaptation,
        },
        content=content,
    )


def _teaching_material_artifact(
    *,
    draft: ContentDraft,
    parent: ContentArtifact,
    task: ContentTask,
    unit: KnowledgeUnit,
    pack: ContextPack,
    run_id: str,
    provider_name: str,
    fixture_adaptation: bool = False,
    attempt: int = 1,
    prompt_version: str = "generate_content_artifact:v1",
    status: Literal["draft", "accepted", "blocked"] = "draft",
) -> ContentArtifact:
    """Create a separately traceable teaching-material artifact."""

    role = "example" if unit.kind == "example" else "proof" if unit.kind == "theorem" else "bridge"
    material = (draft.teaching_material or "").strip()
    if fixture_adaptation and (not material or material.startswith("可配合教学材料") or material.startswith("教学材料建议")):
        material = _focused_teaching_material(unit, pack)
    refs = list(pack.source_refs) if fixture_adaptation else _lossless_refs(draft.source_refs)
    artifact_status: Literal["draft", "accepted", "blocked"] = status
    if not material or not refs:
        artifact_status = "blocked"
    return ContentArtifact(
        artifact_id=f"content-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        version=attempt,
        status=artifact_status,
        source_refs=refs,
        created_by=f"provider:{provider_name}",
        content_type="example" if role == "example" else "explanation",
        knowledge_unit_ids=[unit.artifact_id],
        title=f"{unit.title}：教学材料",
        material_role=role,
        task_id=task.task_id,
        context_pack_id=pack.context_pack_id,
        prompt_version=prompt_version,
        parent_artifact_id=parent.artifact_id,
        metadata={
            "teaching_materials": list(unit.teaching_materials),
            "material_kind": role,
            "material_completeness": "complete_follow_along" if _is_focused_unit(unit) else "guided_prompt",
            "human_review_note": "重点材料仍需按教材原文逐步核对；ContextPack 证据不足时保留待核实标记。" if _is_focused_unit(unit) else None,
            "source_snapshot": pack.source_snapshot,
            "fixture_adaptation": fixture_adaptation,
        },
        content=material,
    )


def _is_focused_unit(unit: KnowledgeUnit) -> bool:
    return any(keyword in unit.title for keyword in ("上确界", "无理数", "绝对值与不等式", "复数"))


def _focused_teaching_material(unit: KnowledgeUnit, pack: ContextPack) -> str:
    """Provide a follow-along worksheet for the four P0 high-risk units.

    The worksheet is still evidence-bound: its source refs come from the
    current ContextPack, and the irrationality item explicitly keeps an
    unresolved marker when the excerpt does not contain the proof steps.
    """

    refs = ", ".join(pack.source_refs[:2]) or "当前 ContextPack 无来源"
    evidence_note = "教材证据：" + refs
    title = unit.title
    if "上确界" in title:
        return (
            "跟做材料：取集合 $S=[0,1)$。第一步，逐项检查 $1$ 是上界；第二步，说明 $1$ 不属于 $S$，"
            "所以 $S$ 没有最大元；第三步，对任意 $\\varepsilon>0$ 取 $x=1-\\varepsilon/2$（当需要时按教材"
            "的范围条件调整），验证 $x\\in S$ 且 $1-\\varepsilon<x$，从而得到 $\\sup S=1$。"
            f"\n\n{evidence_note}"
        )
    if "无理数" in title:
        return (
            "跟做材料：按教材的反证法证明非完全平方数的平方根不是有理数。先假设 $\\sqrt{n}=p/q$"
            " 且 $p,q$ 互素，平方并整理因数分解，再逐步指出素因子指数的矛盾。随后把教材关于 $e$"
            " 无理性的每一个截断、整数性和估计步骤抄写成编号清单；当前片段若没有给出某一步，明确写“待核实”，"
            "不得用外部证明补齐。"
            f"\n\n{evidence_note}"
        )
    if "绝对值与不等式" in title:
        return (
            "跟做材料：从 $(|x|-|y|)^2\\ge 0$ 展开，得到 $2|xy|\\le x^2+y^2$；再把"
            "$x,y$ 替换为向量的内积与范数，逐行核对柯西-施瓦茨不等式，最后取平方根得到"
            "$|x+y|\\le |x|+|y|$ 的三角不等式。每一步在旁边标注使用的教材定义或已证性质。"
            f"\n\n{evidence_note}"
        )
    if "复数" in title:
        return (
            "跟做材料：对 $z=x+iy$ 先计算 $|z|=\\sqrt{x^2+y^2}$，再在复平面标出点 $(x,y)$。"
            "当 $z\\ne0$ 时，按教材选取辐角 $\\theta$，检查"
            "$z=|z|(\\cos\\theta+i\\sin\\theta)=|z|e^{i\\theta}$；最后把 $\\theta$ 限制到主值区间并"
            "用一个象限边界例子核对符号。教材未给出的对数、方根或主值约定保留“待核实”。"
            f"\n\n{evidence_note}"
        )
    return (
        f"跟做材料：围绕“{unit.title}”逐项完成教材材料“{'、'.join(unit.teaching_materials)}”，"
        f"先抄写定义，再用一个教材例子验证。\n\n{evidence_note}"
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
    candidate_artifacts: list[ContentArtifact],
) -> ContentHardCheckResult:
    """Apply non-negotiable source, binding, and notation checks.

    This intentionally does not judge mathematical truth or pedagogical
    quality.  Those belong to later human review and the constrained Critic.
    """

    issues: list[ReviewIssue] = []
    primary = next((artifact for artifact in candidate_artifacts if artifact.parent_artifact_id is None), None)
    if primary is None:
        issues.append(_content_issue(
            task=task,
            suffix="missing-explanation",
            category="coverage",
            severity="blocking",
            message="候选组缺少主讲解 artifact。",
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

    teaching = next((artifact for artifact in candidate_artifacts if artifact.parent_artifact_id == getattr(primary, "artifact_id", None)), None)
    if unit.teaching_materials and (teaching is None or not teaching.content.strip()):
        issues.append(_content_issue(
            task=task,
            suffix="missing-teaching-material",
            category="coverage",
            severity="blocking",
            message="蓝图声明了教学材料，但候选组未提供可跟做的教学材料。",
            target_id=primary.artifact_id if primary is not None else None,
            suggested_action="为每项声明的教学材料补充明确、可跟做的步骤。",
        ))

    pack_refs = set(context_pack.source_refs)
    for artifact in candidate_artifacts:
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
        if artifact is primary:
            formula_latex = str(artifact.metadata.get("formula_latex") or "").strip()
            if formula_latex:
                formula_messages.extend(_formula_format_issues([
                    FormulaNode(id=f"{artifact.artifact_id}-formula", latex=formula_latex),
                ]))
        for index, message in enumerate(formula_messages, start=1):
            issues.append(_content_issue(
                task=task,
                suffix=f"formula-{artifact.artifact_id}-{index}",
                category="formula",
                severity="blocking",
                message=message,
                target_id=artifact.artifact_id,
                suggested_action="使用成对的公式定界符，并将 formula_latex 保持为无外层定界符的 KaTeX。",
            ))

    return ContentHardCheckResult(
        status="accepted" if not issues else "needs_revision",
        issues=issues,
        checked_artifact_id=primary.artifact_id if primary is not None else None,
    )


_CRITIC_BLOCKING_CODES = {
    "objective_missing",
    "prerequisite_unexplained",
    "required_material_not_followable",
}


def _critic_review_issues(
    critique: ContentCritiqueDraft,
    *,
    task: ContentTask,
    primary_artifact: ContentArtifact | None,
    teaching_artifact: ContentArtifact | None,
    attempt: int,
) -> list[ReviewIssue]:
    category_by_code: dict[str, Literal["logic", "coverage", "transition"]] = {
        "objective_missing": "coverage",
        "prerequisite_unexplained": "transition",
        "required_material_not_followable": "coverage",
        "beginner_clarity": "logic",
        "organization": "logic",
        "wording": "logic",
    }
    issues: list[ReviewIssue] = []
    for index, finding in enumerate(critique.issues, start=1):
        target = primary_artifact if finding.target == "content" else teaching_artifact
        severity: Literal["warning", "blocking"] = "blocking" if finding.code in _CRITIC_BLOCKING_CODES else "warning"
        issues.append(_content_issue(
            task=task,
            suffix=f"critic-{attempt}-{index}-{finding.code}",
            category=category_by_code[finding.code],
            severity=severity,
            message=finding.message,
            target_id=target.artifact_id if target is not None else primary_artifact.artifact_id if primary_artifact is not None else None,
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
        material_role="explanation",
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
            "teaching_materials": unit.teaching_materials,
            "source_refs": unit.source_refs,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


