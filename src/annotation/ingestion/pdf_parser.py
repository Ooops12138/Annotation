"""Annotation Hybrid PDF ingestion adapter."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from annotation.config import STORAGE_DIR
from annotation.domain.artifacts import FormulaCandidate, OCRCandidate, SourceBlock, SourceDocument
from annotation.ingestion.hybrid import OCRBackend, resolve_formula_backend, resolve_text_backend

PARSER_VERSION = "annotation-hybrid-pymupdf-v1"


def _sha256(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _iter_text_blocks(page: object) -> Iterable[tuple[float, float, float, float, str, int]]:
    for index, block in enumerate(page.get_text("blocks")):  # type: ignore[attr-defined]
        if len(block) >= 5 and str(block[4]).strip():
            yield (float(block[0]), float(block[1]), float(block[2]), float(block[3]), str(block[4]).strip(), index)


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
        lines.extend([f"## Page {block.page_number} · `{block.source_ref}`", block.raw_text.replace("\n", " ")])
        for candidate in block.ocr_candidates:
            lines.append(f"> OCR candidate ({candidate.recognizer}, {candidate.status}): {candidate.text or '[empty]'}")
        for candidate in block.formula_latex_candidates:
            lines.append(f"> LaTeX candidate ({candidate.recognizer}, {candidate.status}): `{candidate.latex or '[empty]'}`")
        lines.extend([f"> Warning: {warning}" for warning in block.warnings])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_artifacts(document: SourceDocument, blocks: list[SourceBlock], artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": "source-artifact-v1", "document": document.model_dump(mode="json"), "blocks": [b.model_dump(mode="json") for b in blocks]}
    (artifact_dir / "source.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (artifact_dir / "source.md").write_text(_markdown(document, blocks), encoding="utf-8")


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
        for page_number, page in enumerate(document, start=1):
            page_dict = page.get_text("dict")
            raw_blocks = list(page.get_text("blocks"))
            text_indices = [i for i, raw in enumerate(raw_blocks) if len(raw) >= 5 and str(raw[4]).strip()]
            position = 0
            for dict_block in page_dict.get("blocks", []):
                if dict_block.get("type") != 0:
                    continue
                lines = dict_block.get("lines", [])
                text = "\n".join("".join(str(span.get("text", "")) for span in line.get("spans", [])) for line in lines).strip()
                if not text:
                    continue
                bbox = tuple(float(v) for v in dict_block.get("bbox", (0, 0, 0, 0)))
                block_index = text_indices[position] if position < len(text_indices) else position
                position += 1
                source_ref = f"{document_id}-p{page_number}-b{block_index}"
                warnings = _suspicious(text)
                block_type = "formula" if _formula_like(text) else "text"
                ocr_candidates: list[OCRCandidate] = []
                formula_candidates: list[FormulaCandidate] = []
                if warnings or block_type == "formula":
                    if block_type == "formula" and formula_engine is None:
                        formula_engine = resolve_formula_backend(preferred=formula_preference)
                    payload = {"source_ref": source_ref, "bbox": bbox, "candidate_bbox": _candidate_bbox(dict_block, bbox), "block_type": block_type}
                    ocr_candidates, formula_candidates, candidate_warnings = _run_region_candidates(page, payload, region_dir=region_dir, text_backend=text_engine, formula_backend=formula_engine)
                    warnings.extend(candidate_warnings)
                blocks.append(SourceBlock(artifact_id=f"{source_ref}-artifact", source_ref=source_ref, document_id=document_id, run_id=run_id, version=1, status="draft", source_refs=[source_ref], created_by=created_by, page_number=page_number, block_index=block_index, text=text, raw_text=text, text_hash=_sha256(text), parser_version=PARSER_VERSION, bbox=bbox, ocr_candidates=ocr_candidates, formula_latex_candidates=formula_candidates, warnings=list(dict.fromkeys(warnings)), run_metadata={"block_type": block_type, "recognizers": [c.recognizer for c in (*ocr_candidates, *formula_candidates)]}))
    warnings = extraction_warnings(blocks)
    source_document = SourceDocument(artifact_id=document_id, run_id=run_id, version=1, status="draft", created_by=created_by, title=pdf_path.stem, locator=str(pdf_path.resolve()), source_refs=[block.source_ref for block in blocks], warnings=warnings, run_metadata={"parser": "annotation-hybrid", "parser_version": PARSER_VERSION, "sha256": _sha256(pdf_path.read_bytes()), "elapsed_ms": round((time.perf_counter() - started) * 1000), "ocr_backend": text_engine.name, "formula_backend": formula_engine.name if formula_engine else "not_needed", "block_count": len(blocks), "artifact_dir": str(base_dir.resolve()), "json_path": str((base_dir / "source.json").resolve()), "markdown_path": str((base_dir / "source.md").resolve())})
    _write_artifacts(source_document, blocks, base_dir)
    return source_document, blocks
