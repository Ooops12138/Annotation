"""Resumable full OmniDocBench evaluation for Annotation Hybrid.

This evaluates the 1,651 official page images locally. Native annotations are
used only as ground truth; predictions are stored per page so interruptions do
not lose completed work.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any
from concurrent.futures import ProcessPoolExecutor

import sys

if str(Path(__file__).resolve().parents[1] / "src") not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image

from annotation.ingestion.hybrid import Pix2TexBackend, RapidOCRBackend

_TEXT_BACKEND: RapidOCRBackend | None = None


def init_worker() -> None:
    global _TEXT_BACKEND
    _TEXT_BACKEND = RapidOCRBackend()

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "results" / "pdf-benchmark" / "full" / "omnidocbench"


def normalize(value: str) -> str:
    value = value.replace("\n", " ")
    value = re.sub(r"\\left|\\right|\\!|\\,|\\;|\\quad|\\qquad", "", value)
    value = re.sub(r"\s+", "", value)
    return value.replace("$", "").strip()


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, char_left in enumerate(left, 1):
        current = [i]
        for j, char_right in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (char_left != char_right)))
        previous = current
    return previous[-1]


def similarity(left: str, right: str) -> float:
    left, right = normalize(left), normalize(right)
    return 1.0 if not left and not right else 1 - edit_distance(left, right) / max(len(left), len(right), 1)


def crop_box(poly: list[float], image: Image.Image, margin: int = 8) -> tuple[int, int, int, int]:
    return (
        max(0, int(min(poly[0::2]) - margin)),
        max(0, int(min(poly[1::2]) - margin)),
        min(image.width, int(max(poly[0::2]) + margin)),
        min(image.height, int(max(poly[1::2]) + margin)),
    )


def evaluate_page(args: tuple[int, dict[str, Any], str, bool]) -> dict[str, Any]:
    page_index, page, dataset_root, skip_formula = args
    dataset = Path(dataset_root)
    image_path = dataset / "images" / page["page_info"]["image_path"]
    image = Image.open(image_path).convert("RGB")
    global _TEXT_BACKEND
    if _TEXT_BACKEND is None:
        _TEXT_BACKEND = RapidOCRBackend()
    ocr = _TEXT_BACKEND.recognize(image_path)
    gt_text = "\n".join(
        item.get("text", "")
        for item in page.get("layout_dets", [])
        if item.get("category_type") in {"title", "text_block", "header", "footer", "page_number", "page_footnote"}
    )
    formula_rows: list[dict[str, Any]] = []
    if not skip_formula:
        # Full formula inference is intentionally kept out of the worker pool;
        # this path records formula boxes for the separate resumable pass.
        for item in page.get("layout_dets", []):
            if item.get("category_type") == "equation_isolated" and not item.get("ignore"):
                formula_rows.append({"anno_id": item.get("anno_id"), "gold": item.get("latex")})
    return {
        "page_index": page_index,
        "page_no": page["page_info"].get("page_no"),
        "image": str(image_path.relative_to(dataset)),
        "ocr": ocr,
        "text": {"gold_chars": len(normalize(gt_text)), "pred_chars": len(normalize(ocr.get("text") or "")), "similarity": similarity(gt_text, ocr.get("text") or "")},
        "formula": formula_rows,
    }


def run(output: Path, *, limit: int | None = None, skip_formula: bool = False) -> None:
    data = json.loads((DATASET / "OmniDocBench.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    pages_path = output / "pages.jsonl"
    done: dict[int, dict[str, Any]] = {}
    if pages_path.exists():
        for line in pages_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[int(row["page_index"])] = row
    text_backend = RapidOCRBackend()
    formula_backend = None if skip_formula else Pix2TexBackend()
    mode = "a" if pages_path.exists() else "w"
    pending = [(i, page, str(DATASET), skip_formula) for i, page in enumerate(data) if i not in done and (limit is None or i < limit)]
    with pages_path.open(mode, encoding="utf-8") as handle:
        with ProcessPoolExecutor(max_workers=4, initializer=init_worker) as pool:
            for result in pool.map(evaluate_page, pending, chunksize=1):
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                handle.flush()
                done[int(result["page_index"])] = result
                if len(done) % 10 == 0 or len(done) == len(data):
                    print(f"pages={len(done)}/{len(data)} formulas={sum(len(row['formula']) for row in done.values())}", flush=True)
    formula = [item for row in done.values() for item in row["formula"] if item.get("prediction")]
    summary = {
        "dataset": "OmniDocBench v1.6",
        "page_count": len(data),
        "completed_pages": len(done),
        "failed_pages": len(data) - len(done),
        "formula_count": sum(len(row["formula"]) for row in done.values()),
        "formula_completed": len(formula),
        "mean_page_text_similarity": round(sum(row["text"]["similarity"] for row in done.values()) / max(len(done), 1), 4),
        "mean_formula_similarity": round(sum(row["similarity"] for row in formula) / max(len(formula), 1), 4),
        "formula_exact_normalized_rate": round(sum(row["exact_normalized"] for row in formula) / max(len(formula), 1), 4),
        "note": "Page text uses a lightweight concatenated CER proxy; official OmniDocBench matching still requires its evaluator.",
    }
    (output / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "pdf-benchmark" / "full" / "omnidocbench" / "annotation-hybrid")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--skip-formula", action="store_true")
    args = parser.parse_args()
    run(args.output, limit=args.limit, skip_formula=args.skip_formula)
