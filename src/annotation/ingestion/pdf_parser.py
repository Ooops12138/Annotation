"""PyMuPDF4LLM ingestion adapter with explicit local OCR candidates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable

import pymupdf as fitz
import pymupdf4llm

from annotation.config import STORAGE_DIR
from annotation.domain.artifacts import FormulaCandidate, OCRCandidate, SourceBlock, SourceDocument
from annotation.ingestion.hybrid import OCRBackend, resolve_formula_backend, resolve_text_backend

PARSER_VERSION = "annotation-hybrid-pymupdf4llm-v1"


def _sha256(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _iter_markdown_blocks(chunk: dict[str, Any]) -> Iterable[tuple[int, str, tuple[float, float, float, float], str]]:
    """Use layout offsets rather than guessing coordinates from Markdown text."""
    text = chunk["text"]
    boxes = chunk["page_boxes"]
    if text.strip() and not boxes:
        raise ValueError("PyMuPDF4LLM returned text without source layout boxes")
    for box in sorted(boxes, key=lambda item: item["index"]):
        start, stop = box["pos"]
        content = text[start:stop].strip()
        if content:
            yield box["index"], content, tuple(float(v) for v in box["bbox"]), box["class"]


def _suspicious(text: str) -> list[str]:
    if any(char in text for char in ("�", "㊣")):
        return ["source_extraction_warning: suspicious replacement/formula symbol in native text"]
    return []


def _formula_like(text: str) -> bool:
    return bool(re.search(r"(?:[√㊣∑∫≤≥∞→∈⊂]|\\(?:frac|sqrt|sum|int|lim)|[A-Za-z]\s*[=<>])", text))


def extraction_warnings(blocks: list[SourceBlock]) -> list[str]:
    warnings: list[str] = []
    text = " ".join(block.raw_text for block in blocks[:80])
    if text.count("�") >= 3:
        warnings.append("source_extraction_warning: PDF 文本包含替换字符，可能存在字体编码或 OCR 问题。")
    if not blocks:
        warnings.append("source_extraction_warning: PDF 未提取到任何文本块，当前仅保留失败记录。")
    return list(dict.fromkeys(warnings))


def _render_region(page: Any, bbox: tuple[float, float, float, float], path: Path, dpi: int = 300) -> None:
    rect = fitz.Rect(*bbox)
    page_rect = page.rect
    clip = fitz.Rect(max(page_rect.x0, rect.x0 - 4), max(page_rect.y0, rect.y0 - 4), min(page_rect.x1, rect.x1 + 4), min(page_rect.y1, rect.y1 + 4))
    pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), clip=clip, alpha=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(path))


def _candidate_bbox(block: dict[str, Any], bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    lines = []
    for line in block.get("lines", []):
        text = "".join(str(span.get("text", "")) for span in line.get("spans", []))
        if _formula_like(text):
            lines.append(tuple(float(v) for v in line.get("bbox", bbox)))
    return (min(x[0] for x in lines), min(x[1] for x in lines), max(x[2] for x in lines), max(x[3] for x in lines)) if lines else bbox


def _run_region_candidates(page: Any, payload: dict[str, Any], *, region_dir: Path, text_backend: OCRBackend, formula_backend: OCRBackend | None) -> tuple[list[OCRCandidate], list[FormulaCandidate], list[str]]:
    bbox = tuple(payload["candidate_bbox"] or payload["bbox"])
    should_render = text_backend.available or bool(formula_backend and formula_backend.available)
    image_path = region_dir / f"{payload['source_ref']}.png"
    if should_render:
        _render_region(page, bbox, image_path)
    relative_image = str(image_path.relative_to(region_dir.parent)) if should_render else None
    ocr = text_backend.recognize(image_path) if should_render else {"status": "unavailable", "recognizer": text_backend.name, "text": None, "confidence": None, "warnings": ["ocr_disabled_by_configuration"]}
    ocr_candidate = OCRCandidate(candidate_id=f"{payload['source_ref']}-ocr", source_ref=payload["source_ref"], bbox=bbox, status=str(ocr.get("status", "unknown")), recognizer=str(ocr.get("recognizer", text_backend.name)), text=ocr.get("text"), confidence=ocr.get("confidence"), warnings=list(ocr.get("warnings", [])), image_path=relative_image)
    formula_candidates: list[FormulaCandidate] = []
    warnings = list(ocr_candidate.warnings)
    if payload["block_type"] == "formula" and formula_backend is not None:
        formula = formula_backend.recognize(image_path) if should_render else {"status": "unavailable", "recognizer": formula_backend.name, "latex": None, "confidence": None, "warnings": ["formula_disabled_by_configuration"]}
        formula_candidate = FormulaCandidate(candidate_id=f"{payload['source_ref']}-formula", source_ref=payload["source_ref"], bbox=bbox, status=str(formula.get("status", "unknown")), recognizer=str(formula.get("recognizer", formula_backend.name)), latex=formula.get("latex"), confidence=formula.get("confidence"), warnings=list(formula.get("warnings", [])), image_path=relative_image)
        formula_candidates.append(formula_candidate)
        warnings.extend(formula_candidate.warnings)
    return [ocr_candidate], formula_candidates, warnings


def _markdown(document: SourceDocument, blocks: list[SourceBlock]) -> str:
    lines = [f"# {document.title}", "", f"- parser: `{document.run_metadata.get('parser', PARSER_VERSION)}`", f"- run_id: `{document.run_id}`", ""]
    for block in blocks:
        lines.extend([f"## Page {block.page_number} · `{block.source_ref}`", "", block.raw_text])
        for candidate in block.ocr_candidates:
            lines.append(f"> OCR candidate ({candidate.recognizer}, {candidate.status}): {candidate.text or '[empty]'}")
        for candidate in block.formula_latex_candidates:
            lines.append(f"> LaTeX candidate ({candidate.recognizer}, {candidate.status}): `{candidate.latex or '[empty]'}`")
        lines.extend([f"> Warning: {warning}" for warning in block.warnings])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _immutable_artifact_path(path: Path) -> Path:
    if not path.exists():
        return path
    revision = 2
    while True:
        candidate = path.with_name(f"{path.stem}-revision-{revision}{path.suffix}")
        if not candidate.exists():
            return candidate
        revision += 1


def _write_artifacts(document: SourceDocument, blocks: list[SourceBlock], artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    json_path = _immutable_artifact_path(artifact_dir / "source.json")
    markdown_path = _immutable_artifact_path(artifact_dir / "source.md")
    document.run_metadata["json_path"] = str(json_path.resolve())
    document.run_metadata["markdown_path"] = str(markdown_path.resolve())
    payload = {"schema_version": "source-artifact-v1", "document": document.model_dump(mode="json"), "blocks": [b.model_dump(mode="json") for b in blocks]}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown(document, blocks), encoding="utf-8")


def parse_pdf(path: str | Path, *, run_id: str = "run-ingest-local", created_by: str = "parser", ocr_backend: str | None = None, formula_backend: str | None = None, artifact_dir: str | Path | None = None) -> tuple[SourceDocument, list[SourceBlock]]:
    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    started = time.perf_counter()
    document_id = f"srcdoc-{_sha256(pdf_path.read_bytes())[:16]}"
    text_engine = resolve_text_backend(ocr_backend or os.environ.get("ANNOTATION_OCR_BACKEND", "none"))
    formula_preference = formula_backend or os.environ.get("ANNOTATION_FORMULA_BACKEND", "none")
    formula_engine: OCRBackend | None = None
    base_dir = Path(artifact_dir or (STORAGE_DIR / "artifacts" / "ingestion" / run_id))
    region_dir = base_dir / "regions"
    blocks: list[SourceBlock] = []
    with fitz.open(pdf_path) as document:
        toc = document.get_toc()
        page_count = len(document)
        chunks = pymupdf4llm.to_markdown(
            document, page_chunks=True, use_ocr=False,
            write_images=False, embed_images=False, show_progress=False,
        )
        if len(chunks) != page_count:
            raise ValueError("PyMuPDF4LLM page count does not match the PDF")
        empty_text_pages: list[int] = []
        for page_number, chunk in enumerate(chunks, start=1):
            page = document[page_number - 1]
            previous_count = len(blocks)
            for block_index, text, bbox, layout_class in _iter_markdown_blocks(chunk):
                # The new layout has different block boundaries; never alias old refs.
                source_ref = f"{document_id}-{_sha256(PARSER_VERSION)[:8]}-p{page_number}-b{block_index}"
                warnings = _suspicious(text)
                block_type = "formula" if _formula_like(text) else "text"
                ocr_candidates: list[OCRCandidate] = []
                formula_candidates: list[FormulaCandidate] = []
                if warnings or block_type == "formula":
                    if block_type == "formula" and formula_engine is None:
                        formula_engine = resolve_formula_backend(preferred=formula_preference)
                    region = page.get_text("dict", clip=fitz.Rect(*bbox))
                    lines = [line for block in region.get("blocks", []) for line in block.get("lines", [])]
                    payload = {"source_ref": source_ref, "bbox": bbox, "candidate_bbox": _candidate_bbox({"lines": lines}, bbox), "block_type": block_type}
                    ocr_candidates, formula_candidates, candidate_warnings = _run_region_candidates(page, payload, region_dir=region_dir, text_backend=text_engine, formula_backend=formula_engine)
                    warnings.extend(candidate_warnings)
                blocks.append(SourceBlock(artifact_id=f"{source_ref}-artifact", source_ref=source_ref, document_id=document_id, run_id=run_id, version=1, status="draft", source_refs=[source_ref], created_by=created_by, page_number=page_number, block_index=block_index, text=text, raw_text=text, text_hash=_sha256(text), parser_version=PARSER_VERSION, bbox=bbox, ocr_candidates=ocr_candidates, formula_latex_candidates=formula_candidates, warnings=list(dict.fromkeys(warnings)), run_metadata={"block_type": block_type, "layout_class": layout_class, "recognizers": [c.recognizer for c in (*ocr_candidates, *formula_candidates)]}))
            if len(blocks) == previous_count:
                empty_text_pages.append(page_number)
    warnings = extraction_warnings(blocks)
    source_document = SourceDocument(artifact_id=document_id, run_id=run_id, version=1, status="draft", created_by=created_by, title=pdf_path.stem, locator=str(pdf_path.resolve()), source_refs=[block.source_ref for block in blocks], warnings=warnings, run_metadata={"parser": "annotation-hybrid", "parser_version": PARSER_VERSION, "sha256": _sha256(pdf_path.read_bytes()), "elapsed_ms": round((time.perf_counter() - started) * 1000), "ocr_backend": text_engine.name, "formula_backend": formula_engine.name if formula_engine else "not_needed", "block_count": len(blocks), "artifact_dir": str(base_dir.resolve()), "json_path": str((base_dir / "source.json").resolve()), "markdown_path": str((base_dir / "source.md").resolve())})
    source_document.run_metadata.update({"extractor": "pymupdf4llm", "extractor_version": pymupdf4llm.__version__, "toc": toc, "page_count": page_count, "empty_text_pages": empty_text_pages})
    _write_artifacts(source_document, blocks, base_dir)
    return source_document, blocks
