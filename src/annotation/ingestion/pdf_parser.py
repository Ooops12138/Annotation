"""PyMuPDF adapter for creating traceable page/block source artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

try:  # PyMuPDF renamed its import module in newer releases.
    import pymupdf as fitz
except ImportError:  # pragma: no cover - compatibility with older releases
    import fitz  # type: ignore

from annotation.domain.artifacts import SourceBlock, SourceDocument

PARSER_VERSION = "pymupdf-blocks-v1"


def extraction_warnings(blocks: list[SourceBlock]) -> list[str]:
    """Return visible quality warnings without mutating extracted evidence."""
    text = " ".join(block.text for block in blocks[:80])
    warnings: list[str] = []
    replacement_count = text.count("�")
    if replacement_count >= 3:
        warnings.append("source_extraction_warning: PDF 文本包含替换字符，可能存在字体编码或 OCR 问题。")
    if len(blocks) == 0:
        warnings.append("source_extraction_warning: PDF 未提取到任何文本块，当前 P0 不执行 OCR。")
    return warnings


def _sha256(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _iter_text_blocks(page: object) -> Iterable[tuple[float, float, float, float, str, int]]:
    """Yield non-empty text blocks with their original page block index."""
    for index, block in enumerate(page.get_text("blocks")):  # type: ignore[attr-defined]
        if len(block) < 5:
            continue
        text = str(block[4]).strip()
        if not text:
            continue
        yield (float(block[0]), float(block[1]), float(block[2]), float(block[3]), text, index)


def parse_pdf(path: str | Path, *, run_id: str = "run-ingest-local", created_by: str = "parser") -> tuple[SourceDocument, list[SourceBlock]]:
    """Parse a text PDF into a source document and traceable source blocks.

    The parser deliberately does not OCR or normalize text. Each block keeps its
    page number, source reference, bounding box, text hash, and parser version so
    later quality checks can identify extraction problems without losing evidence.
    """
    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    document_id = f"srcdoc-{_sha256(pdf_path.read_bytes())[:16]}"
    source_document = SourceDocument(
        artifact_id=document_id,
        run_id=run_id,
        version=1,
        status="draft",
        created_by=created_by,
        title=pdf_path.stem,
        locator=str(pdf_path.resolve()),
    )
    blocks: list[SourceBlock] = []
    with fitz.open(pdf_path) as document:
        for page_number, page in enumerate(document, start=1):
            for x0, y0, x1, y1, text, original_index in _iter_text_blocks(page):
                source_ref = f"{document_id}-p{page_number}-b{original_index}"
                blocks.append(
                    SourceBlock(
                        artifact_id=f"{source_ref}-artifact",
                        source_ref=source_ref,
                        document_id=document_id,
                        run_id=run_id,
                        version=1,
                        status="draft",
                        source_refs=[source_ref],
                        created_by=created_by,
                        page_number=page_number,
                        block_index=original_index,
                        text=text,
                        text_hash=_sha256(text),
                        parser_version=PARSER_VERSION,
                        bbox=(x0, y0, x1, y1),
                    )
                )
    source_document.source_refs = [block.source_ref for block in blocks]
    return source_document, blocks
