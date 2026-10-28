"""Summarize actual usage by provider/model without printing textbook prompts."""

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from annotation.token_estimation import base_token_estimate


def calibrate(path: Path) -> list[dict]:
    groups = defaultdict(list)
    for line in path.open(encoding="utf-8"):
        try:
            record = json.loads(line)
            actual = record.get("usage", {}).get("prompt_tokens", 0)
            inputs = record.get("input", {})
            text = "\n".join(str(inputs.get(key) or "") for key in ("system_prompt", "prompt"))
            base = base_token_estimate(text)
            if actual > 0 and base > 200:
                groups[(record["provider"], record["model"])].append(actual / base)
        except (ValueError, KeyError, TypeError):
            continue
    results = []
    for (provider, model), values in sorted(groups.items()):
        values.sort()
        p95 = values[math.ceil(len(values) * .95) - 1]
        results.append(dict(provider=provider, model=model, samples=len(values),
                            median_ratio=statistics.median(values), p95_ratio=p95,
                            max_ratio=max(values), suggested_scale=math.ceil(max(values) * 1.05 * 100) / 100))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_path", type=Path)
    args = parser.parse_args()
    print(json.dumps(calibrate(args.log_path), ensure_ascii=False, indent=2))
