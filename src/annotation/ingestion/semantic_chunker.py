"""Deterministic, source-preserving semantic chunks over extracted PDF blocks.

The policy follows common layout-aware chunking practice: preserve element order,
keep paragraph boundaries, attach short headings to their following text, and
bound chunks by a target and hard character limit rather than splitting blindly.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from annotation.domain.artifacts import SourceBlock


def _is_heading(text: str) -> bool:
    value = " ".join(text.split()).strip()
    return bool(value and len(value) <= 48 and (
        re.match(r"^(第[一二三四五六七八九十百0-9]+[章节篇部分]|[一二三四五六七八九十]+[、.．])", value)
        or (value.endswith(("：", ":")) and len(value) <= 36)
        or (len(value) <= 24 and not re.search(r"[。；;，,]", value) and not re.search(r"[=+*/<>≤≥]", value))
    ))


def build_semantic_blocks(blocks: Iterable[SourceBlock], *, target_chars: int = 480,
                          max_chars: int = 900, overlap_blocks: int = 0) -> list[SourceBlock]:
    """Group adjacent raw blocks without changing them; lineage is in source_refs."""
    if type(target_chars) is not int or type(max_chars) is not int or type(overlap_blocks) is not int:
        raise ValueError("chunk sizes and overlap must be integers")
    if target_chars < 1 or max_chars < target_chars or overlap_blocks < 0:
        raise ValueError("require 1 <= target_chars <= max_chars and overlap_blocks >= 0")
    ordered = sorted(blocks, key=lambda b: (b.document_id, b.page_number, b.block_index))
    groups: list[list[SourceBlock]] = []
    current: list[SourceBlock] = []
    text_len = 0
    for block in ordered:
        text = block.text.strip()
        if not text:
            continue
        boundary = current and (block.document_id != current[-1].document_id or block.page_number != current[-1].page_number)
        if boundary:
            groups.append(current); current = []; text_len = 0
        heading = _is_heading(text)
        if current and (text_len >= target_chars or text_len + len(text) + 1 > max_chars or heading):
            groups.append(current); current = []; text_len = 0
        # Oversized source elements stay intact and are isolated; source text is never truncated.
        current.append(block)
        text_len += len(text) + (1 if len(current) > 1 else 0)
    if current:
        groups.append(current)

    result: list[SourceBlock] = []
    for index, group in enumerate(groups):
        text = "\n".join(b.text.strip() for b in group)
        refs = [b.source_ref for b in group]
        identity = hashlib.sha256((group[0].document_id + "|" + "|".join(refs) + "|" + text).encode()).hexdigest()[:16]
        page = group[0].page_number
        ref = f"{group[0].document_id}-sem-p{page}-c{index:05d}-{identity}"
        result.append(group[0].model_copy(update={
            "artifact_id": ref, "source_ref": ref, "source_refs": refs,
            "block_index": group[0].block_index, "text": text, "raw_text": text,
            "text_hash": hashlib.sha256(text.encode()).hexdigest(),
            "parser_version": group[0].parser_version + "+semantic-v1",
            "run_metadata": {**group[0].run_metadata, "chunk_type": "semantic", "chunk_index": index,
                             "first_block_index": group[0].block_index,
                             "last_block_index": group[-1].block_index},
        }))
    if overlap_blocks and result:
        # Explicitly unsupported in v1: overlapping lineage makes qrel mapping ambiguous.
        raise ValueError("overlap_blocks must be 0 in semantic-chunker-v1")
    return result
