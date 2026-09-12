"""Download the official OmniDocBench annotation and all page images."""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pdf-benchmark" / "full" / "omnidocbench"
API = "https://huggingface.co/api/datasets/opendatalab/OmniDocBench/tree/main/images"
BASE = "https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/main/"


def get_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": "Annotation/0.1"})
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def list_all_images() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    next_url: str | None = f"{API}?recursive=false&expand=false&limit=1000"
    while True:
        request = Request(next_url, headers={"User-Agent": "Annotation/0.1"})
        with urlopen(request, timeout=120) as response:
            batch = json.loads(response.read().decode("utf-8"))
            link = response.headers.get("Link", "")
        entries.extend(item for item in batch if item.get("type") == "file")
        next_url = next((part.strip().split(";", 1)[0].strip("<>") for part in link.split(",") if 'rel="next"' in part), None)
        if not next_url:
            return entries


def download_one(item: dict[str, object]) -> tuple[str, str | None]:
    path = str(item["path"])
    relative = Path(path).relative_to("images")
    target = OUT / "images" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == int(item.get("size", 0)):
        return path, None
    url = BASE + quote(path, safe="/")
    try:
        request = Request(url, headers={"User-Agent": "Annotation/0.1"})
        with urlopen(request, timeout=180) as response:
            target.write_bytes(response.read())
        return path, None
    except Exception as exc:
        return path, str(exc)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    annotation = OUT / "OmniDocBench.json"
    if not annotation.exists() or annotation.stat().st_size < 40_000_000:
        request = Request(
            "https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/main/OmniDocBench.json?download=true",
            headers={"User-Agent": "Annotation/0.1"},
        )
        with urlopen(request, timeout=300) as response:
            annotation.write_bytes(response.read())
    items = list_all_images()
    manifest = {"dataset": "OmniDocBench", "image_count": len(items), "images": items}
    (OUT / "download-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    failures: list[dict[str, str]] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(download_one, item) for item in items]
        for future in as_completed(futures):
            path, error = future.result()
            completed += 1
            if error:
                failures.append({"path": path, "error": error})
            if completed % 25 == 0 or completed == len(items):
                print(f"downloaded {completed}/{len(items)} failures={len(failures)}", flush=True)
    (OUT / "download-failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"image_count": len(items), "completed": completed, "failures": len(failures)}, ensure_ascii=False))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
