"""Adapt current page OCR JSONL into OmniDocBench markdown prediction files."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "results" / "pdf-benchmark" / "full" / "omnidocbench" / "annotation-hybrid" / "pages.jsonl"
OUTPUT = ROOT / "results" / "pdf-benchmark" / "full" / "omnidocbench" / "annotation-hybrid-predictions"


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    count = 0
    for line in INPUT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        image_name = Path(row["image"]).name
        target = OUTPUT / f"{Path(image_name).stem}.md"
        target.write_text((row.get("ocr", {}).get("text") or "") + "\n", encoding="utf-8")
        count += 1
    manifest = {"prediction_dir": str(OUTPUT), "page_predictions": count, "source": str(INPUT)}
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
