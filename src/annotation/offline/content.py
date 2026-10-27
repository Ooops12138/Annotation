"""Deterministic content fixtures for the explicit offline profile."""

from annotation.domain.artifacts import ContentCritiqueDraft, ContextPack, KnowledgeUnit
from annotation.workflow.models import ContentDraft


def content_draft(unit: KnowledgeUnit, pack: ContextPack) -> ContentDraft:
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
            f"学习目标是：{'；'.join(unit.objective_texts) or '掌握本单元的基本含义和用法'}。"
            f"教材证据摘录：{evidence or '当前没有可用的教材摘录，需要人工审核。'}"
        ),
        source_refs=refs,
        callouts=[],
    )


def content_critique() -> ContentCritiqueDraft:
    return ContentCritiqueDraft(issues=[])
