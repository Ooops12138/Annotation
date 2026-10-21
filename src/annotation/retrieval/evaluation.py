"""Frozen-corpus retrieval evaluation, independent of embedding implementations."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import fmean
from time import perf_counter

from annotation.domain.artifacts import SourceBlock
from annotation.fact_checking.contracts import SearchStatus

KS = (1, 3, 5, 10)
SUCCESS = {SearchStatus.OK, SearchStatus.NO_RESULTS}


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_dataset(corpus_path: str | Path, questions_path: str | Path):
    rows = [json.loads(line) for line in Path(corpus_path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not rows:
        raise ValueError("corpus must not be empty")
    blocks, seen = [], set()
    for row in rows:
        if row["source_ref"] in seen:
            raise ValueError("duplicate corpus source_ref")
        seen.add(row["source_ref"])
        if hashlib.sha256(row["text"].encode()).hexdigest() != row["text_sha256"]:
            raise ValueError("corpus text hash mismatch")
        blocks.append(SourceBlock(
            artifact_id=row["source_ref"], source_ref=row["source_ref"], document_id=row["document_id"],
            run_id="retrieval-evaluation", version=1, status="draft", source_refs=[row["source_ref"]],
            created_by="frozen-corpus", page_number=row["page_number"], block_index=row["block_index"],
            text=row["text"], text_hash=row["text_hash"], parser_version=row["parser_version"], bbox=(0, 0, 0, 0),
        ))
    dataset = json.loads(Path(questions_path).read_text(encoding="utf-8-sig"))
    if dataset["corpus_sha256"] != sha256_file(corpus_path):
        raise ValueError("question set was annotated against a different corpus snapshot")
    by_ref = {r["source_ref"]: r for r in rows}
    ids = set()
    for q in dataset["questions"]:
        if not q["id"] or q["id"] in ids or not q["query"].strip():
            raise ValueError("question IDs must be unique and queries nonempty")
        ids.add(q["id"])
        if type(q["answerable"]) is not bool or not q["category"]:
            raise ValueError("invalid question category/answerable flag")
        positives = 0
        for ref, label in q["qrels"].items():
            if ref not in by_ref or by_ref[ref]["text_sha256"] != label["text_sha256"]:
                raise ValueError("qrel source or source hash mismatch")
            if type(label["grade"]) is not int or label["grade"] not in (0, 1, 2):
                raise ValueError("relevance grade must be 0, 1 or 2")
            positives += label["grade"] > 0
        if bool(positives) != q["answerable"]:
            raise ValueError("answerable questions require positive qrels; unanswerable questions must not have them")
    if not ids:
        raise ValueError("question set must not be empty")
    return blocks, dataset


def ranking_metrics(ranked_refs: list[str], qrels: dict, ks=KS) -> dict[str, float]:
    if len(ranked_refs) != len(set(ranked_refs)):
        raise ValueError("ranking contains duplicate source references")
    grades = {ref: x["grade"] if isinstance(x, dict) else x for ref, x in qrels.items()}
    relevant = {ref for ref, grade in grades.items() if grade > 0}
    if not relevant:
        raise ValueError("ranking metrics require at least one relevant source")
    result = {}
    for k in ks:
        top = ranked_refs[:k]
        hits = [i + 1 for i, ref in enumerate(top) if ref in relevant]
        dcg = sum((2 ** grades.get(ref, 0) - 1) / math.log2(i + 2) for i, ref in enumerate(top))
        ideal = sum((2 ** grade - 1) / math.log2(i + 2)
                    for i, grade in enumerate(sorted(grades.values(), reverse=True)[:k]))
        result.update({f"hit@{k}": float(bool(hits)), f"recall@{k}": len(hits) / len(relevant),
                       f"mrr@{k}": 1 / hits[0] if hits else 0.0,
                       f"ndcg@{k}": dcg / ideal if ideal else 0.0})
    return result


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def evaluate_tool(tool, questions: list[dict], blocks: list[SourceBlock], *, repeats=3):
    if repeats < 1:
        raise ValueError("repeats must be positive")
    by_ref = {b.source_ref: b for b in blocks}
    records = []
    # Warmup is explicitly excluded. No query embeddings are cached by the tool.
    warmup_error = None
    try:
        warmup = tool.search(questions[0]["query"], limit=max(KS))
        if warmup.status not in SUCCESS:
            warmup_error = warmup.status.value
    except Exception as exc:
        warmup_error = type(exc).__name__
    for q in questions:
        samples, outcomes, first = [], [], None
        for _ in range(repeats):
            started = perf_counter()
            try:
                result = tool.search(q["query"], limit=max(KS))
                elapsed = (perf_counter() - started) * 1000
                hits, seen = [], set()
                for rank, e in enumerate(result.evidence, 1):
                    source = by_ref.get(e.source_ref)
                    if (source is None or source.text_hash != e.text_hash or e.source_ref in seen
                            or source.document_id != e.document_id or source.page_number != e.page_number
                            or source.block_index != e.block_index):
                        raise ValueError("retrieved evidence failed source traceability validation")
                    seen.add(e.source_ref)
                    hits.append({"position": rank, "source_ref": e.source_ref, "page_number": e.page_number,
                                 "block_index": e.block_index, "text_hash": e.text_hash, "text": e.text,
                                 "score": e.rank, "grade": q["qrels"].get(e.source_ref, {}).get("grade", 0)})
                outcome = {"status": result.status.value, "error_code": result.error_code.value if result.error_code else None,
                           "metadata": dict(result.metadata), "source_refs": [h["source_ref"] for h in hits]}
            except Exception as exc:
                elapsed = (perf_counter() - started) * 1000
                hits = []
                outcome = {"status": "error", "error_code": type(exc).__name__, "metadata": {}, "source_refs": []}
            samples.append(elapsed)
            outcomes.append(outcome)
            if first is None:
                first = {**outcome, "hits": hits}
        refs = first["source_refs"] if first["status"] in {s.value for s in SUCCESS} else []
        records.append({"id": q["id"], "query": q["query"], "category": q["category"],
                        "answerable": q["answerable"], "rationale": q.get("rationale", ""),
                        "qrels": q["qrels"], **first,
                        "metrics": ranking_metrics(refs, q["qrels"]) if q["answerable"] else {},
                        "latency_ms": samples, "rounds": outcomes,
                        "ranking_stable": all(o["source_refs"] == first["source_refs"] and o["status"] == first["status"] for o in outcomes)})
    answered = [r for r in records if r["answerable"]]
    unanswered = [r for r in records if not r["answerable"]]
    def average(rows):
        return {key: fmean(r["metrics"][key] for r in rows) for key in rows[0]["metrics"]} if rows else {}
    latencies = [x for r in records for x in r["latency_ms"]]
    summary = {
        "questions": len(records), "answerable": len(answered), "unanswerable": len(unanswered),
        "warmup_error": warmup_error,
        "metrics": average(answered),
        "by_category": {cat: average([r for r in answered if r["category"] == cat]) for cat in sorted({r["category"] for r in answered})},
        "latency_ms": {"p50": percentile(latencies, .5), "p95": percentile(latencies, .95), "samples": len(latencies)},
        "unanswerable_empty_rate": (sum(r["status"] == "no_results" and not r["hits"] for r in unanswered) / len(unanswered)) if unanswered else None,
        "error_rounds": sum(o["status"] not in {s.value for s in SUCCESS} for r in records for o in r["rounds"]),
        "degraded_rounds": sum(bool(o["metadata"].get("degraded")) for r in records for o in r["rounds"]),
        "unstable_questions": sum(not r["ranking_stable"] for r in records),
    }
    return summary, records


def render_report(report: dict, records: list[dict]) -> str:
    lines = ["# 教材检索开发基线", "", f"状态：{report['status']}；标注未经独立人工复核，未标注块按不相关计。",
             "仅比较当前教材原始块，不能推广为通用模型排名；指标不衡量最终生成内容质量。", "",
             f"语料：{report['corpus']['blocks']} 块 / {report['corpus']['pages']} 页；SHA256：`{report['corpus']['sha256']}`。",
             f"编码器：`{json.dumps(report.get('encoder'), ensure_ascii=False, sort_keys=True)}`。", "",
             "Recall/Hit/MRR 的相关阈值为 grade>0；nDCG 增益为 2^grade−1，按问题宏平均。",
             "排名指标使用首轮；延迟包含查询编码，预热后每题三轮（可用 --repeats 调整），不启用查询缓存。",
             "失败问题按零分进入宏平均；无答案空结果率不把错误当作正确拒答。", "",
             "| 方法 | Recall@5 | nDCG@5 | Hit@5 | MRR@5 | p50 ms | p95 ms | 无答案空结果率 | 错误轮数 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, result in report["methods"].items():
        if "failure" in result:
            lines.append(f"| {name} | 失败：{result['failure']} | | | | | | | |")
            continue
        m, lat = result["metrics"], result["latency_ms"]
        def fmt(value):
            return f"{value:.4f}" if value is not None else "N/A"
        lines.append(f"| {name} | {fmt(m.get('recall@5'))} | {fmt(m.get('ndcg@5'))} | {fmt(m.get('hit@5'))} | {fmt(m.get('mrr@5'))} | {fmt(lat['p50'])} | {fmt(lat['p95'])} | {fmt(result['unanswerable_empty_rate'])} | {result['error_rounds']} |")
    if all("metrics" in report["methods"].get(name, {}) for name in ("fts", "vector", "hybrid")):
        fts, vector, hybrid = [report["methods"][name]["metrics"] for name in ("fts", "vector", "hybrid")]
        lines += ["", f"本次向量相对 FTS5 的 Recall@5 差值为 {vector['recall@5'] - fts['recall@5']:+.4f}；"
                  f"混合相对纯向量的 nDCG@5 差值为 {hybrid['ndcg@5'] - vector['ndcg@5']:+.4f}。",
                  "FTS5 保留现有中文整句查询和子串回退策略，未加入中文分词；自然语言改写可能没有词面命中。",
                  "向量相似度没有设置拒答阈值，因此无答案误召回不能视为已经解决。"]
    lines += ["", "## 索引和运行信息", "", "```json", json.dumps(report["setup"], ensure_ascii=False, indent=2), "```",
              "", "## 按题型比较", "", "| 方法 | 题型 | Recall@5 | nDCG@5 |", "|---|---|---:|---:|"]
    for name, result in report["methods"].items():
        for category, m in result.get("by_category", {}).items():
            lines.append(f"| {name} | {category} | {m['recall@5']:.4f} | {m['ndcg@5']:.4f} |")
    lines += ["", "## 未召回相关来源的样例", ""]
    for name in report["methods"]:
        failures = [r for r in records if r["method"] == name and r["answerable"] and r["metrics"]["hit@5"] == 0][:4]
        for r in failures:
            actual = ", ".join(h["source_ref"] for h in r["hits"][:3]) or "空结果"
            lines.append(f"- **{name} / {r['id']}**：{r['query']}；前列来源：{actual}。")
    lines += ["", "## 无答案误召回样例", ""]
    for name in report["methods"]:
        for r in [x for x in records if x["method"] == name and not x["answerable"] and x["hits"]][:2]:
            lines.append(f"- **{name} / {r['id']}**：{r['query']} → {r['hits'][0]['source_ref']}（grade 0）。")
    lines += ["", "## 复现", "", "```powershell", report["command"], "```", "",
              "API 未配置或未运行时，不表示已完成真实 API 验证。不同编码器必须分别建索引和生成报告。",
              "原始解析块较碎，公式可能分散到多个块；这是本基线保留的输入限制，不能归因于检索器一种因素。", ""]
    return "\n".join(lines)
