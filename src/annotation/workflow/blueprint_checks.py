"""Deterministic quality gate for a generated Learning Blueprint."""

from __future__ import annotations

from annotation.domain.artifacts import BlueprintCheckResult, LearningBlueprint, ReviewIssue


def validate_blueprint(
    blueprint: LearningBlueprint,
    *,
    valid_source_refs: set[str],
) -> BlueprintCheckResult:
    """Check minimum coverage, source integrity and prerequisite references."""
    issues: list[ReviewIssue] = []
    unit_ids = {unit.artifact_id for unit in blueprint.knowledge_units}
    unit_titles = {unit.title for unit in blueprint.knowledge_units}
    covered_kinds = sorted({unit.kind for unit in blueprint.knowledge_units})

    if len(blueprint.knowledge_units) < 3:
        issues.append(
            ReviewIssue(
                issue_id="blueprint-insufficient-coverage",
                category="coverage",
                severity="blocking",
                message="Learning Blueprint 至少需要 3 个知识单元。",
                target_id=blueprint.artifact_id,
            )
        )

    if len(unit_ids) != len(blueprint.knowledge_units):
        issues.append(
            ReviewIssue(
                issue_id="blueprint-duplicate-unit-id",
                category="logic",
                severity="blocking",
                message="Learning Blueprint 的 knowledge_unit_id 必须唯一。",
                target_id=blueprint.artifact_id,
            )
        )

    for unit in blueprint.knowledge_units:
        if not unit.learning_objectives:
            issues.append(
                ReviewIssue(
                    issue_id=f"blueprint-missing-objective-{unit.artifact_id}",
                    category="coverage",
                    severity="warning",
                    message=f"知识单元“{unit.title}”缺少学习目标。",
                    target_id=unit.artifact_id,
                )
            )
        if not unit.source_refs:
            issues.append(
                ReviewIssue(
                    issue_id=f"blueprint-missing-source-{unit.artifact_id}",
                    category="source",
                    severity="blocking",
                    message=f"知识单元“{unit.title}”缺少教材来源。",
                    target_id=unit.artifact_id,
                )
            )
        elif not set(unit.source_refs).issubset(valid_source_refs):
            issues.append(
                ReviewIssue(
                    issue_id=f"blueprint-invalid-source-{unit.artifact_id}",
                    category="source",
                    severity="blocking",
                    message=f"知识单元“{unit.title}”包含无效 source_ref。",
                    target_id=unit.artifact_id,
                )
            )
        unresolved = [
            prerequisite
            for prerequisite in unit.prerequisites
            if prerequisite not in unit_ids and prerequisite not in unit_titles
        ]
        if unresolved:
            issues.append(
                ReviewIssue(
                    issue_id=f"blueprint-invalid-prerequisite-{unit.artifact_id}",
                    category="logic",
                    severity="blocking",
                    message=f"知识单元“{unit.title}”包含未解析的前置关系：{', '.join(unresolved)}。",
                    target_id=unit.artifact_id,
                )
            )
        unresolved_related = [related for related in unit.related_unit_ids if related not in unit_ids]
        if unresolved_related:
            issues.append(
                ReviewIssue(
                    issue_id=f"blueprint-invalid-related-{unit.artifact_id}",
                    category="logic",
                    severity="blocking",
                    message=f"知识单元“{unit.title}”包含未解析的 related_unit_ids：{', '.join(unresolved_related)}。",
                    target_id=unit.artifact_id,
                    suggested_action="使用同一 Blueprint 中存在的稳定 knowledge_unit_id，不能用标题猜测修复。",
                )
            )

    if not ({"formula", "theorem", "example"} & set(covered_kinds)):
        issues.append(
            ReviewIssue(
                issue_id="blueprint-missing-teaching-material",
                category="coverage",
                severity="warning",
                message="蓝图尚未表达公式、定理或例题类教学材料。",
                target_id=blueprint.artifact_id,
            )
        )

    blocking = any(issue.severity == "blocking" for issue in issues)
    return BlueprintCheckResult(
        status="needs_revision" if blocking else "accepted",
        issues=issues,
        covered_kinds=covered_kinds,
        checked_unit_ids=sorted(unit_ids),
    )
