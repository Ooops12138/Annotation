"""Small, dependency-light runner for the PDF extraction benchmark.

The script intentionally keeps the benchmark protocol separate from the
production parser.  It can always run the Annotation Hybrid native-text
baseline, and it records external-tool availability without pretending that a
missing model/runtime is a score of zero.

Examples:

    python scripts/pdf_benchmark.py status
    python scripts/pdf_benchmark.py run --scheme annotation-hybrid \
        --input books/数学分析第1章.pdf

Marker and MinerU are run through explicit commands supplied by the caller;
their outputs must be adapted to the canonical JSON contract before metrics
are compared.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - compatibility with project envs
    import fitz  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "results" / "pdf-benchmark"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from annotation.ingestion.hybrid import OCRBackend, resolve_formula_backend, resolve_text_backend


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def suspicious_chars(text: str) -> list[str]:
    """Return symbols that commonly indicate broken PDF text mappings."""
    checks = {
        "replacement_character": "�",
        "circled_ideograph_used_as_radical": "㊣",
        "private_use_area": None,
    }
    found = [name for name, char in checks.items() if char and char in text]
    if any(0xE000 <= ord(char) <= 0xF8FF for char in text):
        found.append("private_use_area")
    return found


def classify_block(text: str, *, font_size: float | None = None) -> str:
    stripped = " ".join(text.split())
    if not stripped:
        return "unknown"
    if font_size and font_size >= 14:
        return "heading"
    if stripped.startswith(("定理", "公理", "定义", "引理", "证明")):
        return "heading"
    formula_markers = ("√", "㊣", "∑", "∫", "≤", "≥", "∞", "→", "∈", "⊂")
    if any(marker in stripped for marker in formula_markers):
        return "formula"
    return "text"


def is_formula_like(text: str) -> bool:
    """Avoid sending ordinary Chinese prose to a formula recognizer."""
    compact = "".join(text.split())
    if not compact:
        return False
    math_symbols = sum(char in "√㊣∑∫≤≥∞→∈⊂∂∇∀∃≠≈±×÷" for char in compact)
    has_cjk = any("\u3400" <= char <= "\u9fff" for char in compact)
    if math_symbols >= 2 and (not has_cjk or math_symbols / max(len(compact), 1) >= 0.08):
        return True
    if not has_cjk and any(char in compact for char in "=+-*/^()[]{}"):
        return True
    return len(compact) <= 36 and not has_cjk and any(char.isdigit() for char in compact)


def _render_region(page: Any, bbox: tuple[float, float, float, float], image_path: Path, dpi: int) -> None:
    """Render one evidence region without altering the native text."""
    rect = fitz.Rect(*bbox)
    page_rect = page.rect
    # A small margin helps OCR see diacritics and neighboring fraction bars.
    margin = 3.0
    clip = fitz.Rect(
        max(page_rect.x0, rect.x0 - margin),
        max(page_rect.y0, rect.y0 - margin),
        min(page_rect.x1, rect.x1 + margin),
        min(page_rect.y1, rect.y1 + margin),
    )
    scale = dpi / 72.0
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(image_path))


def _region_candidate(
    *,
    page: Any,
    block: dict[str, Any],
    page_number: int,
    output_dir: Path,
    text_backend: OCRBackend,
    formula_backend: OCRBackend,
    dpi: int,
) -> dict[str, Any]:
    safe_ref = block["source_ref"].replace("/", "_")
    image_path = output_dir / "regions" / f"{safe_ref}.png"
    render_bbox = tuple(block.get("candidate_bbox") or block["bbox"])
    _render_region(page, render_bbox, image_path, dpi)
    text_result = text_backend.recognize(image_path)
    formula_result = None
    if block["block_type"] == "formula" and is_formula_like(block["raw_text"]):
        formula_result = formula_backend.recognize(image_path)
    warnings = list(text_result.get("warnings", []))
    if formula_result:
        warnings.extend(formula_result.get("warnings", []))
    return {
        "page_number": page_number,
        "source_ref": block["source_ref"],
        "bbox": list(render_bbox),
        "image_path": str(image_path.relative_to(output_dir)),
        "native_text": block["raw_text"],
        "ocr": text_result,
        "formula": formula_result,
        "status": "success" if any(
            result and result.get("status") == "success" for result in (text_result, formula_result)
        ) else "unavailable" if all(
            not result or result.get("status") == "unavailable" for result in (text_result, formula_result)
        ) else "review",
        "warnings": warnings,
    }


def extract_annotation_hybrid(
    pdf_path: Path,
    *,
    output_dir: Path | None = None,
    ocr_backend: str = "auto",
    formula_command: str | None = None,
    formula_backend: str = "auto",
    render_dpi: int = 300,
) -> dict[str, Any]:
    """Extract native text while preserving enough evidence for review.

    This is deliberately conservative: no text is silently repaired.  A
    suspicious block carries a warning and remains eligible for a later local
    OCR/formula-recognition candidate.
    """
    document_id = f"srcdoc-{sha256_file(pdf_path)[:16]}"
    started = time.perf_counter()
    pages: list[dict[str, Any]] = []
    regions: list[dict[str, Any]] = []
    text_backend = resolve_text_backend(ocr_backend)
    formula_engine = resolve_formula_backend(formula_command, preferred=formula_backend)
    with fitz.open(pdf_path) as document:
        for page_number, page in enumerate(document, start=1):
            blocks: list[dict[str, Any]] = []
            page_dict = page.get_text("dict")
            # Keep the same original PDF block indices as the production parser.
            # ``dict`` may include image/drawing blocks, so its enumerate index
            # is not a stable SourceBlock index when a page mixes text and
            # non-text content.
            text_block_indices = [
                index
                for index, raw_block in enumerate(page.get_text("blocks"))
                if len(raw_block) >= 5 and str(raw_block[4]).strip()
            ]
            text_block_position = 0
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                lines = block.get("lines", [])
                spans = [span for line in lines for span in line.get("spans", [])]
                text = "\n".join(
                    "".join(str(span.get("text", "")) for span in line.get("spans", []))
                    for line in lines
                ).strip()
                if not text:
                    continue
                bbox = tuple(float(value) for value in block.get("bbox", (0, 0, 0, 0)))
                font_size = max((float(span.get("size", 0)) for span in spans), default=0.0)
                warnings = suspicious_chars(text)
                block_index = (
                    text_block_indices[text_block_position]
                    if text_block_position < len(text_block_indices)
                    else text_block_position
                )
                text_block_position += 1
                block_payload = {
                        "source_ref": f"{document_id}-p{page_number}-b{block_index}",
                        "bbox": list(bbox),
                        "block_type": classify_block(text, font_size=font_size),
                        "text": text,
                        "raw_text": text,
                        "latex": None,
                        "confidence": None,
                        "recognizer": "pymupdf",
                        "font_size": font_size,
                        "warnings": warnings,
                    }
                formula_lines = []
                for line in lines:
                    line_text = "".join(str(span.get("text", "")) for span in line.get("spans", []))
                    if is_formula_like(line_text) or any(marker in line_text for marker in ("√", "㊣", "∑", "∫", "≤", "≥", "∞", "→", "∈", "⊂")):
                        line_bbox = tuple(float(value) for value in line.get("bbox", bbox))
                        formula_lines.append(line_bbox)
                if formula_lines:
                    block_payload["candidate_bbox"] = [
                        min(item[0] for item in formula_lines),
                        min(item[1] for item in formula_lines),
                        max(item[2] for item in formula_lines),
                        max(item[3] for item in formula_lines),
                    ]
                blocks.append(block_payload)
                if output_dir and (warnings or block_payload["block_type"] == "formula"):
                    regions.append(
                        _region_candidate(
                            page=page,
                            block=block_payload,
                            page_number=page_number,
                            output_dir=output_dir,
                            text_backend=text_backend,
                            formula_backend=formula_engine,
                            dpi=render_dpi,
                        )
                    )
            pages.append({"page_number": page_number, "blocks": blocks})

    all_text = "\n".join(block["text"] for page in pages for block in page["blocks"])
    return {
        "schema_version": "pdf-extraction-v1",
        "document": {
            "document_id": document_id,
            "source_path": str(pdf_path.resolve()),
            "sha256": sha256_file(pdf_path),
            "parser": "annotation-hybrid",
            "parser_version": "pymupdf-native-v1",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "page_count": len(pages),
            "ocr_backend": text_backend.name,
            "formula_backend": formula_engine.name,
        },
        "pages": pages,
        "regions": regions,
        "summary": {
            "block_count": sum(len(page["blocks"]) for page in pages),
            "formula_candidate_count": sum(
                block["block_type"] == "formula" for page in pages for block in page["blocks"]
            ),
            "suspicious_block_count": sum(
                bool(block["warnings"]) for page in pages for block in page["blocks"]
            ),
            "suspicious_symbols": sorted(set(suspicious_chars(all_text))),
            "local_region_count": len(regions),
            "local_region_success_count": sum(region["status"] == "success" for region in regions),
            "local_region_unavailable_count": sum(region["status"] == "unavailable" for region in regions),
            "formula_recognition_region_count": sum(
                region.get("formula") is not None for region in regions
            ),
            "formula_recognition_success_count": sum(
                region.get("formula", {}).get("status") == "success" for region in regions if region.get("formula")
            ),
        },
    }


def to_markdown(payload: dict[str, Any]) -> str:
    document = payload["document"]
    lines = [
        f"# {Path(document['source_path']).stem}",
        "",
        f"- parser: `{document['parser']}@{document['parser_version']}`",
        f"- pages: {document['page_count']}",
        f"- local OCR: `{document.get('ocr_backend')}`",
        f"- formula OCR: `{document.get('formula_backend')}`",
        f"- local regions: {payload['summary'].get('local_region_count', 0)}",
        "",
    ]
    for page in payload["pages"]:
        lines.extend([f"## Page {page['page_number']}", ""])
        for block in page["blocks"]:
            ref = block["source_ref"]
            text = block["text"].replace("\n", " ")
            if block["block_type"] == "heading":
                lines.append(f"### {text}")
            elif block["block_type"] == "formula":
                lines.append(f"`[formula candidate]` {text}")
            else:
                lines.append(text)
            warning = ", ".join(block["warnings"])
            if warning:
                lines.append(f"> Warning ({ref}): {warning}")
            else:
                lines.append(f"<!-- source_ref: {ref} -->")
            regions = [region for region in payload.get("regions", []) if region["source_ref"] == ref]
            for region in regions:
                lines.append(
                    f"<!-- local_region: {region['image_path']} status={region['status']} -->"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "document.json"
    markdown_path = output_dir / "document.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(to_markdown(payload), encoding="utf-8")
    formula_candidates = [
        {
            "page_number": page["page_number"],
            "source_ref": block["source_ref"],
            "bbox": block["bbox"],
            "candidate_bbox": block.get("candidate_bbox"),
            "raw_text": block["raw_text"],
            "latex": block["latex"],
            "confidence": block["confidence"],
            "recognizer": block["recognizer"],
            "warnings": block["warnings"],
            "local_regions": [
                region for region in payload.get("regions", []) if region["source_ref"] == block["source_ref"]
            ],
            "latex_candidates": [
                region["formula"].get("latex")
                for region in payload.get("regions", [])
                if region["source_ref"] == block["source_ref"]
                and region.get("formula")
                and region["formula"].get("latex")
            ],
        }
        for page in payload["pages"]
        for block in page["blocks"]
        if block["block_type"] == "formula"
    ]
    (output_dir / "formula-candidates.json").write_text(
        json.dumps(formula_candidates, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "local-ocr-candidates.json").write_text(
        json.dumps(payload.get("regions", []), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return json_path, markdown_path


def command_status(command: str) -> dict[str, Any]:
    path = shutil.which(command)
    if not path:
        return {"command": command, "available": False, "path": None}
    try:
        completed = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15)
        version = (completed.stdout or completed.stderr).strip().splitlines()[:1]
    except Exception as exc:  # pragma: no cover - environment dependent
        version = [f"version probe failed: {exc}"]
    return {"command": command, "available": True, "path": path, "version": version[0] if version else None}


def run_external(scheme: str, pdf_path: Path, output_dir: Path, command: str | None) -> int:
    if not command:
        print(json.dumps({"scheme": scheme, "status": "not_run", "reason": "--command is required"}, ensure_ascii=False))
        return 2
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    completed = subprocess.run(command, shell=True, cwd=ROOT, capture_output=True, text=True)
    result = {
        "scheme": scheme,
        "input": str(pdf_path),
        "status": "success" if completed.returncode == 0 else "error",
        "returncode": completed.returncode,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "note": "External output must be normalized to pdf-extraction-v1 before scoring.",
    }
    (output_dir / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show optional parser command availability")
    run = sub.add_parser("run", help="run one scheme on one PDF")
    run.add_argument("--scheme", choices=["annotation-hybrid", "marker-balanced", "mineru-hybrid"], required=True)
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, default=None)
    run.add_argument("--command", help="external command for Marker/MinerU; shell expansion is caller-controlled")
    run.add_argument("--ocr-backend", choices=["auto", "rapidocr", "tesseract", "none"], default="auto")
    run.add_argument("--formula-command", help="local formula OCR command template containing {image}")
    run.add_argument("--formula-backend", choices=["auto", "none"], default="auto")
    run.add_argument("--render-dpi", type=int, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        status = {
            "python": sys.version,
            "platform": platform.platform(),
            "pymupdf": getattr(fitz, "__version__", "unknown"),
            "tools": [command_status(command) for command in ("marker_single", "mineru", "tesseract", "pdftotext")],
        }
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0

    pdf_path = args.input.resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"PDF not found: {pdf_path}")
    output_dir = (args.output_dir or DEFAULT_OUTPUT / args.scheme / pdf_path.stem).resolve()
    if args.scheme == "annotation-hybrid":
        payload = extract_annotation_hybrid(
            pdf_path,
            output_dir=output_dir,
            ocr_backend=args.ocr_backend,
            formula_command=args.formula_command,
            formula_backend=args.formula_backend,
            render_dpi=args.render_dpi,
        )
        json_path, markdown_path = write_outputs(payload, output_dir)
        print(
            json.dumps(
                {
                    "status": "success",
                    "json": str(json_path),
                    "markdown": str(markdown_path),
                    "formula_candidates": str(output_dir / "formula-candidates.json"),
                    "local_ocr_candidates": str(output_dir / "local-ocr-candidates.json"),
                    "summary": payload["summary"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    return run_external(args.scheme, pdf_path, output_dir, args.command)


if __name__ == "__main__":
    raise SystemExit(main())
