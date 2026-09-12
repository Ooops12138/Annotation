"""Apply OmniDocBench's official text normalization/edit-distance formula to existing pages."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import Levenshtein  # type: ignore


def main() -> int:
    root = ROOT / "results" / "pdf-benchmark" / "full" / "omnidocbench"
    rows = [json.loads(line) for line in (root / "annotation-hybrid" / "pages.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    gt = json.loads((root / "OmniDocBench.json").read_text(encoding="utf-8"))
    by_index = {i: page for i, page in enumerate(gt)}
    scores = []
    for row in rows:
        page = by_index[row["page_index"]]
        gold = "\n".join(item.get("text", "") for item in page.get("layout_dets", []) if item.get("category_type") in {"title", "text_block", "header", "footer", "page_number", "page_footnote"})
        pred = row.get("ocr", {}).get("text") or ""
        # OmniDocBench's normalized_text ultimately applies clean_string for
        # ordinary text. Existing OCR output is plain page text, so use that
        # deterministic official cleaning stage here (formula blocks are
        # reported separately and are not folded into this text score).
        clean = lambda value: re.sub(r"[^\w\u4e00-\u9fff]", "", value.replace("\\t", "").replace("\\n", "").replace("\t", "").replace("\n", ""))
        gold, pred = clean(gold), clean(pred)
        distance = Levenshtein.distance(pred, gold)
        upper = max(len(pred), len(gold))
        scores.append({"page_index": row["page_index"], "image": row["image"], "edit_distance": distance, "upper_len": upper, "edit_dist": distance / upper if upper else 0.0, "normalized_similarity": 1 - distance / upper if upper else 1.0})
    total_distance = sum(item["edit_distance"] for item in scores)
    total_upper = sum(item["upper_len"] for item in scores)
    result = {
        "metric": "OmniDocBench clean_string normalization + Levenshtein Edit_dist",
        "dataset": "OmniDocBench v1.6 completed subset",
        "page_count": len(scores),
        "coverage": len(scores) / len(gt),
        "edit_dist_all_total": total_distance / total_upper if total_upper else 0.0,
        "edit_dist_page_avg": sum(item["edit_dist"] for item in scores) / max(len(scores), 1),
        "similarity_all_total": 1 - total_distance / total_upper if total_upper else 1.0,
        "similarity_page_avg": sum(item["normalized_similarity"] for item in scores) / max(len(scores), 1),
        "note": "This applies the official text normalization and Levenshtein formula to existing OCR page outputs; official end-to-end matching/layout/formula metrics require structured Markdown predictions and were not imputed.",
        "pages": scores,
    }
    out = root / "annotation-hybrid" / "official-text-metrics-existing.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "pages"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
