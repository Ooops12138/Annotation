"""Bounded context construction for T-006 content generation.

The source index remains the durable store.  This module creates a small,
auditable view for one content task instead of passing a whole chapter to a
model call.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from annotation.domain.artifacts import (
    ContextExcerpt,
    ContextPack,
    ContentTask,
    KnowledgeUnit,
    LearningBlueprint,
    SourceBlock,
)


def estimate_tokens(text: str) -> int:
    """Conservative, dependency-free estimate suitable for a safety budget."""

    return max(0, math.ceil(len(text) / 4))


def render_context_pack(pack: ContextPack) -> str:
    lines = [
        f"ContextPack {pack.context_pack_id}; input_budget_tokens={pack.input_budget_tokens}; "
        f"estimated_input_tokens={pack.estimated_input_tokens}",
        "证据片段（仅可使用这些来源支持教材事实）：",
    ]
    lines.extend(
        f"[{excerpt.source_ref} p{excerpt.page_number}-b{excerpt.block_index}] {excerpt.text}"
        for excerpt in pack.excerpts
    )
    if pack.prerequisite_titles:
        lines.append("直接前置知识单元：" + "、".join(pack.prerequisite_titles))
    if pack.omitted_source_refs:
        lines.append("因上下文预算未纳入的来源（不得据此补写事实）：" + ", ".join(pack.omitted_source_refs))
    return "\n".join(lines)


def _compact(text: str, limit: int = 1400) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _terms(unit: KnowledgeUnit) -> list[str]:
    values = [unit.title, *unit.learning_objectives, *unit.teaching_materials]
    terms: list[str] = []
    for value in values:
        for term in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}|\d+", value):
            if term not in terms:
                terms.append(term)
    return terms


def _lexical_candidates(unit: KnowledgeUnit, blocks: Iterable[SourceBlock]) -> list[SourceBlock]:
    terms = _terms(unit)
    scored: list[tuple[int, int, SourceBlock]] = []
    for block in blocks:
        text = block.text or ""
        score = sum(text.count(term) for term in terms)
        if score:
            scored.append((score, -(block.page_number * 10000 + block.block_index), block))
    scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored]


def select_source_refs(
    *,
    title: str,
    learning_objectives: list[str],
    teaching_materials: list[str],
    blocks: list[SourceBlock],
    limit: int = 3,
) -> list[str]:
    """Find a small evidence seed when a model omitted source refs."""

    unit = KnowledgeUnit(
        artifact_id="selection-only",
        run_id="selection-only",
        version=1,
        status="draft",
        created_by="context-builder",
        title=title,
        kind="concept",
        learning_objectives=learning_objectives,
        teaching_materials=teaching_materials,
    )
    candidates = _lexical_candidates(unit, blocks)
    return [block.source_ref for block in candidates[:limit]]


def build_context_pack(
    task: ContentTask,
    unit: KnowledgeUnit,
    blueprint: LearningBlueprint,
    blocks: list[SourceBlock],
    *,
    context_window: int | None = None,
    reserved_output_tokens: int = 2200,
    safety_margin_tokens: int = 400,
) -> ContextPack:
    """Select primary evidence first, then bounded neighbors and lexical hits."""

    effective_window = context_window or 12000
    input_budget = max(0, effective_window - reserved_output_tokens - safety_margin_tokens)
    # Chinese-heavy textbook text can tokenize close to one token per
    # character.  Three characters per estimated token is intentionally
    # conservative and leaves room for prompt/schema overhead.
    char_budget = input_budget * 3
    by_ref = {block.source_ref: block for block in blocks}
    ordered_blocks = sorted(blocks, key=lambda block: (block.page_number, block.block_index))

    primary = [by_ref[ref] for ref in task.source_refs if ref in by_ref]
    if not primary:
        primary = _lexical_candidates(unit, ordered_blocks)[:3]

    candidates: list[tuple[str, SourceBlock]] = [("source_refs", block) for block in primary]
    primary_keys = {(block.page_number, block.block_index) for block in primary}
    for block in primary:
        for neighbor in ordered_blocks:
            if neighbor.page_number == block.page_number and abs(neighbor.block_index - block.block_index) <= 1:
                if (neighbor.page_number, neighbor.block_index) not in primary_keys:
                    candidates.append(("adjacent_block", neighbor))
                    primary_keys.add((neighbor.page_number, neighbor.block_index))
    for block in _lexical_candidates(unit, ordered_blocks):
        if (block.page_number, block.block_index) not in primary_keys:
            candidates.append(("keyword_fallback", block))
            primary_keys.add((block.page_number, block.block_index))

    excerpts: list[ContextExcerpt] = []
    selected_refs: list[str] = []
    omitted_refs: list[str] = []
    used_chars = 0
    strategies: list[str] = []
    for strategy, block in candidates:
        text = _compact(block.text)
        if not text:
            continue
        if len(text) > char_budget:
            if block.source_ref in task.source_refs and block.source_ref not in omitted_refs:
                omitted_refs.append(block.source_ref)
            continue
        if used_chars + len(text) > char_budget and excerpts:
            if block.source_ref in task.source_refs and block.source_ref not in omitted_refs:
                omitted_refs.append(block.source_ref)
            continue
        excerpts.append(ContextExcerpt(source_ref=block.source_ref, page_number=block.page_number, block_index=block.block_index, text=text))
        selected_refs.append(block.source_ref)
        used_chars += len(text)
        if strategy not in strategies:
            strategies.append(strategy)
        if used_chars >= char_budget:
            break

    for ref in task.source_refs:
        if ref not in selected_refs and ref not in omitted_refs:
            omitted_refs.append(ref)

    prerequisites = [
        prerequisite
        for prerequisite in unit.prerequisites
        if any(candidate.title == prerequisite for candidate in blueprint.knowledge_units)
    ]
    snapshot_material = "|".join(f"{excerpt.source_ref}:{by_ref[excerpt.source_ref].text_hash}" for excerpt in excerpts if excerpt.source_ref in by_ref)
    source_snapshot = hashlib.sha256(snapshot_material.encode("utf-8")).hexdigest() if snapshot_material else None
    rendered_chars = sum(len(excerpt.text) for excerpt in excerpts)
    return ContextPack(
        context_pack_id=f"ctx-{task.task_id}",
        run_id=task.run_id,
        task_id=task.task_id,
        knowledge_unit_id=task.knowledge_unit_id,
        source_refs=selected_refs,
        selected_source_refs=selected_refs,
        excerpts=excerpts,
        prerequisite_titles=prerequisites,
        estimated_input_tokens=math.ceil(rendered_chars / 4),
        input_budget_tokens=input_budget,
        omitted_source_refs=omitted_refs,
        retrieval_strategy=strategies or ["source_refs"],
        source_snapshot=source_snapshot,
    )


def write_content_run_artifact(
    *,
    run_id: str,
    blueprint_version: str,
    tasks: list[ContentTask],
    context_packs: list[ContextPack],
    artifacts: list[Any],
    checks: dict[str, str],
    provider_metadata: dict[str, Any],
    root: str | Path,
) -> Path:
    """Persist the complete T-006 handoff without relying on graph memory."""

    safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip(".-") or "run"
    artifact_dir = Path(root) / safe_run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / "content.json"
    payload = {
        "run_id": run_id,
        "blueprint_version": blueprint_version,
        "tasks": [task.model_dump(mode="json") for task in tasks],
        "context_packs": [pack.model_dump(mode="json") for pack in context_packs],
        "content_artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        "checks": checks,
        "provider_metadata": provider_metadata,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
