import json
from pathlib import Path

root = Path('results/pdf-benchmark/full/omnidocbench')
rows = [json.loads(line) for line in (root/'annotation-hybrid/pages.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
indices = {row['page_index'] for row in rows}
gt = json.loads((root/'OmniDocBench.json').read_text(encoding='utf-8'))
subset = [page for i,page in enumerate(gt) if i in indices]
(root/'OmniDocBench_completed_subset.json').write_text(json.dumps(subset,ensure_ascii=False),encoding='utf-8')
(root/'annotation-hybrid-predictions-completed').mkdir(exist_ok=True)
for row in rows:
    src = root/'annotation-hybrid-predictions'/f"{Path(row['image']).stem}.md"
    dst = root/'annotation-hybrid-predictions-completed'/src.name
    if src.exists(): dst.write_text(src.read_text(encoding='utf-8'),encoding='utf-8')
print(len(subset))
