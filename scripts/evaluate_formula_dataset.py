"""Evaluate the local formula recognizer on the OmniDocBench demo formula subset."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from PIL import Image

from annotation.ingestion.hybrid import Pix2TexBackend

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tmp" / "omnidocbench" / "demo.json"
IMAGES = ROOT / "tmp" / "omnidocbench" / "images"
OUTPUT = ROOT / "results" / "pdf-benchmark" / "formula" / "omnidocbench-demo"


def normalize(value: str) -> str:
    value = value.replace("\n", " ")
    value = re.sub(r"\\left|\\right|\\!|\\,|\\;|\\quad|\\qquad", "", value)
    value = re.sub(r"\s+", "", value)
    value = value.replace("$", "")
    return value.strip()


def edit_similarity(left: str, right: str) -> float:
    previous = list(range(len(right) + 1))
    for i, char_left in enumerate(left, start=1):
        current = [i]
        for j, char_right in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (char_left != char_right),
            ))
        previous = current
    distance = previous[-1]
    return 1.0 if not left and not right else 1 - distance / max(len(left), len(right), 1)


def main() -> int:
    samples: list[dict[str, Any]] = []
    for page_index, page in enumerate(json.loads(DATA.read_text(encoding="utf-8"))):
        image_path = IMAGES / page["page_info"]["image_path"]
        image = Image.open(image_path).convert("RGB")
        for item in page["layout_dets"]:
            if item.get("category_type") != "equation_isolated" or item.get("ignore"):
                continue
            poly = item["poly"]
            bbox = (
                max(0, int(min(poly[0::2]) - 8)),
                max(0, int(min(poly[1::2]) - 8)),
                min(image.width, int(max(poly[0::2]) + 8)),
                min(image.height, int(max(poly[1::2]) + 8)),
            )
            crop_path = OUTPUT / "crops" / f"p{page_index}-a{item['anno_id']}.png"
            crop_path.parent.mkdir(parents=True, exist_ok=True)
            image.crop(bbox).save(crop_path)
            samples.append({"page_index": page_index, "anno_id": item["anno_id"], "bbox": bbox, "gold": item["latex"], "path": crop_path})

    backend = Pix2TexBackend()
    started = time.perf_counter()
    results = []
    for sample in samples:
        prediction = backend.recognize(sample["path"])
        gold = normalize(sample["gold"])
        predicted = normalize(prediction.get("latex") or "")
        results.append({
            "page_index": sample["page_index"],
            "anno_id": sample["anno_id"],
            "crop": str(sample["path"].relative_to(OUTPUT)),
            "gold": sample["gold"],
            "prediction": prediction,
            "normalized_similarity": round(edit_similarity(gold, predicted), 4),
            "exact_normalized": gold == predicted,
        })
        print(f"{len(results)}/{len(samples)} similarity={results[-1]['normalized_similarity']:.3f}")
    summary = {
        "dataset": "OmniDocBench demo formula subset",
        "sample_count": len(results),
        "exact_normalized_count": sum(item["exact_normalized"] for item in results),
        "exact_normalized_rate": round(sum(item["exact_normalized"] for item in results) / max(len(results), 1), 4),
        "mean_normalized_similarity": round(sum(item["normalized_similarity"] for item in results) / max(len(results), 1), 4),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "recognizer": backend.name,
        "note": "This is the repository demo subset, not the full OmniDocBench official score.",
        "results": results,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
