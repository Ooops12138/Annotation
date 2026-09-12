"""Create a transparent report from the currently completed benchmark pages."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results" / "pdf-benchmark"


def main() -> int:
    pages_path = RESULTS / "full" / "omnidocbench" / "annotation-hybrid" / "pages.jsonl"
    rows = [json.loads(line) for line in pages_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    formula_demo = json.loads((RESULTS / "formula" / "omnidocbench-demo" / "metrics.json").read_text(encoding="utf-8"))
    page_count = 1651
    mean_text = sum(row["text"]["similarity"] for row in rows) / max(len(rows), 1)
    formula_labels = sum(len(row["formula"]) for row in rows)
    report = f"""# Annotation Hybrid 阶段性评估报告

生成时间：2026-09-09

## 结论

本报告基于已落盘结果生成。按用户要求，已暂停长时间全量任务；不会把部分结果称为完整官方基准分数。

当前推荐：保留 Annotation Hybrid 作为 POC 输入基线，RapidOCR 负责局部文字候选，pix2tex 负责局部公式候选；所有 OCR/LaTeX 结果继续作为候选证据，不能静默覆盖原文。

## OmniDocBench 当前进度

| 指标 | 当前结果 |
|---|---:|
| 官方页数 | {page_count} |
| 已完成页数 | {len(rows)} |
| 页覆盖率 | {len(rows) / page_count:.2%} |
| 已记录公式标注 | {formula_labels} |
| RapidOCR 成功页 | {sum(row['ocr']['status'] == 'success' for row in rows)} |
| 页级文本相似度代理均值 | {mean_text:.4f} |

页级文本相似度是本地快速代理指标，不是 OmniDocBench 官方 Edit Distance/匹配分数；官方评估器尚未对这批预测执行，因此不能与论文或排行榜分数直接比较。

结果明细：`results/pdf-benchmark/full/omnidocbench/annotation-hybrid/pages.jsonl`

## 公式专项

OmniDocBench 官方仓库 demo 公式子集（17 个样本）已完成 pix2tex 评估：

- 归一化编辑相似度均值：`{formula_demo['mean_normalized_similarity']:.4f}`
- 归一化完全匹配率：`{formula_demo['exact_normalized_rate']:.2%}`

这 17 个样本是 demo 子集，不是完整公式标注集。当前机器 CPU 推理；复杂多行公式和混合正文区域错误较明显，公式结果必须经过质量门审核。

## 当前教材迁移测试

- 25 页、1206 个原生文本块；
- 127 个局部区域；
- 16 个可疑 block；
- 43 个行级公式区域返回 pix2tex LaTeX 候选；
- 根号等字体映射异常仍保留为原始证据和 warning。

结果目录：`results/pdf-benchmark/annotation-hybrid/math-ch1-pix2tex-final/`

## 未完成项目

1. OmniDocBench 剩余 {page_count - len(rows)} 页；
2. OmniDocBench 官方 evaluator 的正式文本/公式/版面指标；
3. 2066 个公式区域的全量 pix2tex 识别；
4. olmOCR-Bench 全量下载和官方评估；
5. Marker/MinerU 对照（按当前指示暂不执行）。
"""
    output = RESULTS / "partial-report.md"
    output.write_text(report, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
