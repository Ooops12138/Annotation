"""Read-only audit of a saved learning run; never imports a model provider."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def audit_run(manifest_path: Path, database: Path | None = None) -> tuple[dict, dict]:
    manifest_path = manifest_path.resolve()
    manifest = read_json(manifest_path)
    run_id = manifest["run_id"]
    findings: list[dict] = []
    inventory: dict[str, dict] = {}
    payloads: dict[str, dict] = {}

    def issue(code: str, target: str, detail: str, severity: str = "blocking") -> None:
        findings.append(dict(code=code, target=target, detail=detail, severity=severity))

    # Paths in current manifests are absolute; relative ones are manifest-relative.
    for kind in ("source_json", "blueprint", "content", "quiz", "fact_check",
                 "interactive_components", "document", "review"):
        raw = manifest.get("paths", {}).get(kind)
        if not raw:
            issue("missing_artifact", kind, "运行清单没有该阶段的产物路径。")
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = manifest_path.parent / path
        try:
            data = path.read_bytes()
            payloads[kind] = read_json(path)
            inventory[kind] = dict(path=str(path.resolve()), sha256=hashlib.sha256(data).hexdigest(),
                                   schema_version=payloads[kind].get("schema_version"))
        except (OSError, ValueError) as exc:
            issue("unreadable_artifact", kind, f"{type(exc).__name__}: {exc}")

    source = payloads.get("source_json", {})
    blocks = {b["source_ref"]: b for b in source.get("blocks", [])}
    valid_refs = set(blocks)
    blueprint = payloads.get("blueprint", {}).get("blueprint") or {}
    units = {u["artifact_id"]: u for u in blueprint.get("knowledge_units", [])}
    content = payloads.get("content", {})
    tasks = {t["task_id"]: t for t in content.get("tasks", [])}
    packs = {p["context_pack_id"]: p for p in content.get("context_packs", [])}
    explanations = content.get("content_artifacts", [])
    quizzes = content.get("quiz_artifacts", [])
    document = payloads.get("document", {}).get("document") or {}
    review = payloads.get("review", {}).get("report") or {}

    def binding(value: dict, target: str) -> None:
        if value.get("run_id") != run_id:
            issue("run_binding", target, f"run_id={value.get('run_id')!r}，预期 {run_id!r}。")

    def references(value: Any, target: str) -> None:
        if isinstance(value, dict):
            if "source_refs" in value:
                unknown = sorted(set(value["source_refs"]) - valid_refs)
                if unknown:
                    issue("unknown_source", target, ", ".join(unknown))
            for key, child in value.items():
                references(child, f"{target}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                references(child, f"{target}[{index}]")

    # Rejected candidates in traces may intentionally preserve invalid refs.
    # Audit final projections, never turn historical failed attempts into new failures.
    for kind, value in (("blueprint", blueprint), ("tasks", list(tasks.values())),
                        ("packs", list(packs.values())), ("content", explanations),
                        ("quiz", quizzes), ("document", document)):
        references(value, kind)
    for kind, data in payloads.items():
        if "run_id" in data:
            binding(data, kind)
    for name, values in (("source", list(blocks.values())), ("units", list(units.values())),
                         ("tasks", list(tasks.values())), ("packs", list(packs.values())),
                         ("content", explanations), ("quiz", quizzes)):
        for value in values:
            binding(value, f"{name}:{value.get('artifact_id', value.get('task_id', ''))}")
    for name, value in (("blueprint", blueprint), ("document", document), ("review", review)):
        if value:
            binding(value, name)
    if not blocks or not units or not tasks or not explanations or not quizzes:
        issue("empty_pipeline", run_id, "来源、知识单元、任务、讲解或题目 artifact 为空（单个题目数组允许为空）。")
    task_units = Counter(t.get("knowledge_unit_id") for t in tasks.values())
    if set(task_units) != set(units) or any(n != 1 for n in task_units.values()):
        issue("task_coverage", "tasks", "任务未恰好覆盖每个 Blueprint 单元一次。")
    for task in tasks.values():
        if task.get("content_types") != ["explanation", "quiz"]:
            issue("task_contract", task["task_id"], "content_types 不是 explanation + quiz。")
    expected_blueprint = f"{blueprint.get('artifact_id')}:v{blueprint.get('version')}"
    if content.get("blueprint_version") != expected_blueprint:
        issue("blueprint_binding", "content", "内容快照指向不同 Blueprint 版本。")
    for task in tasks.values():
        if task.get("blueprint_version") != expected_blueprint:
            issue("blueprint_binding", task["task_id"], "任务指向不同 Blueprint 版本。")

    for kind, artifacts in (("content", explanations), ("quiz", quizzes)):
        counts = Counter(a.get("task_id") for a in artifacts)
        if set(counts) != set(tasks) or any(n != 1 for n in counts.values()):
            issue("artifact_coverage", kind, "最终 artifact 未恰好覆盖每个任务一次。")
        for artifact in artifacts:
            target = artifact["artifact_id"]
            task = tasks.get(artifact.get("task_id"), {})
            pack = packs.get(artifact.get("context_pack_id"), {})
            unit_id = task.get("knowledge_unit_id")
            if not task or not pack or pack.get("task_id") != task.get("task_id") or pack.get("knowledge_unit_id") != unit_id:
                issue("context_binding", target, "任务、知识单元和 ContextPack 绑定不一致。")
            if artifact.get("knowledge_unit_ids") != [unit_id]:
                issue("unit_binding", target, "artifact 的知识单元与任务不一致。")
            if not artifact.get("source_refs") or not set(artifact["source_refs"]).issubset(pack.get("source_refs", [])):
                issue("context_source", target, "来源为空或超出本次 ContextPack。")
            if artifact.get("status") != "accepted":
                issue("artifact_not_accepted", target, str(artifact.get("status")))
            if kind == "content" and not artifact.get("content", "").strip():
                issue("empty_explanation", target, "讲解正文为空。")
            if kind == "quiz":
                questions = artifact.get("questions", [])
                if "question_count" in artifact and artifact["question_count"] != len(questions):
                    issue("question_count", target, "声明题量与数组长度不同。")
                for question in questions:
                    qid = question.get("question_id", target)
                    if question.get("knowledge_unit_id") != unit_id:
                        issue("question_unit", qid, "题目绑定了不同单元。")
                    objectives = set(question.get("target_objectives", []))
                    if not objectives or not objectives.issubset(units.get(unit_id, {}).get("learning_objectives", [])):
                        issue("question_objective", qid, "题目目标为空或不属于知识单元。")
                    options = question.get("options", [])
                    if len(options) < 2 or len(set(options)) != len(options) or options.count(question.get("answer")) != 1:
                        issue("question_answer", qid, "选项/唯一答案结构无效。")
                    if not question.get("explanation") or not question.get("source_refs") or not set(question["source_refs"]).issubset(pack.get("source_refs", [])):
                        issue("question_evidence", qid, "解析或当前 ContextPack 来源缺失。")
    independent = payloads.get("quiz", {})
    if quizzes != independent.get("quiz_artifacts") or content.get("quiz_coverage") != independent.get("quiz_coverage"):
        issue("quiz_snapshot_mismatch", "quiz", "独立题目快照与 content 快照不同。")

    nodes = [node for section in document.get("sections", []) for node in section.get("children", [])]
    projected_quizzes = {n.get("id"): n for n in nodes if n.get("type") == "quiz"}
    questions = [q for a in quizzes for q in a.get("questions", [])]
    expected_questions = {q["question_id"]: q for q in questions}
    if document:
        if document.get("blueprint_version") not in {None, expected_blueprint}:
            issue("blueprint_binding", "document", "文档指向不同 Blueprint 版本。")
        if any(n.get("type") not in {"markdown", "callout", "quiz", "interactive_component"} for n in nodes):
            issue("unknown_node", "document", "存在当前 IR 白名单之外的节点。")
        if set(projected_quizzes) != set(expected_questions) or len(projected_quizzes) != sum(n.get("type") == "quiz" for n in nodes):
            issue("quiz_projection", "document", "页面题目丢失、重复或出现未知题目。")
        for qid in projected_quizzes.keys() & expected_questions.keys():
            for field in ("question", "options", "answer", "explanation", "source_refs"):
                if projected_quizzes[qid].get(field) != expected_questions[qid].get(field):
                    issue("quiz_projection", qid, f"页面 {field} 与题目 artifact 不同。")
        for artifact in explanations:
            if not any(n.get("type") == "markdown" and n.get("content") == artifact.get("content") for n in nodes):
                issue("content_projection", artifact["artifact_id"], "最终讲解未完整投影为 Markdown。")
        if document.get("review_report_id") != review.get("report_id") or review.get("document_id") != document.get("document_id"):
            issue("review_binding", "document", "文档和审核报告绑定不一致。")
        if document.get("status") == "published" and review.get("status") != "passed":
            issue("unauthorized_publication", "document", "未通过审核却标为 published。")
    stages = {kind: payloads.get(kind, {}).get("status") for kind in ("fact_check", "interactive_components")}
    for kind, status in stages.items():
        if status in {"blocked", "failed"}:
            issue("stage_blocked", kind, f"阶段状态为 {status}。")
        elif status not in {"accepted", "passed", "at_risk", "not_needed"}:
            issue("stage_unknown", kind, f"缺少可识别的终态：{status!r}。")
    if review.get("status") != "passed":
        severity = "blocking" if review.get("status") in {"blocked", "needs_revision"} else "warning"
        issue("review_not_passed", "review", f"审核状态为 {review.get('status')!r}。", severity)
    if document and document.get("status") not in {"accepted", "published"}:
        issue("document_not_readable", "document", f"文档状态为 {document['status']}。")
    issue("manual_review_pending", run_id, "本工具只做结构与证据链检查；数学正确性、教学质量和页面体验仍需复核。", "warning")

    database_run = None
    database_latest_manifest = None
    if database is not None:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT run_id,status,provider,model,origin FROM runs WHERE run_id=?", (run_id,)).fetchone()
            database_run = dict(row) if row else None
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'").fetchone():
                latest = connection.execute("SELECT path FROM artifacts WHERE run_id=? AND kind='run' ORDER BY version DESC LIMIT 1", (run_id,)).fetchone()
                database_latest_manifest = latest[0] if latest else None
        stale = database_latest_manifest is not None and Path(database_latest_manifest).resolve() != manifest_path
        if stale:
            issue("historical_manifest", run_id, f"正在审计历史快照；SQLite 最新清单是 {database_latest_manifest}。", "warning")
        if not database_run:
            issue("database_run_missing", run_id, "SQLite 没有对应运行。")
        elif not stale and database_run["status"] == "succeeded" and (not document or not review or any(s in {"blocked", "failed"} for s in stages.values())):
            issue("database_status_conflict", run_id, "SQLite 为 succeeded，但缺少文档/审核或某阶段已阻塞。")

    def sample(kind: str, artifact: dict, unit_id: str) -> dict:
        refs = artifact.get("source_refs", [])
        return dict(kind=kind, knowledge_unit_id=unit_id, unit_title=units.get(unit_id, {}).get("title"),
                    artifact=artifact, evidence=[dict(source_ref=ref, page_number=blocks[ref]["page_number"],
                    text=blocks[ref]["text"]) for ref in refs if ref in blocks], human_verdict="pending")

    # Prefer proof-heavy units; then fill the four slots in curriculum order.
    preferred = [a for a in explanations if any(term in (a.get("title") or "") for term in ("上确界", "无理", "柯西", "复数", "复对数"))]
    selected = (preferred + [a for a in explanations if a not in preferred])[:4]
    samples = [sample("explanation", a, (a.get("knowledge_unit_ids") or [""])[0]) for a in selected]
    # One question from distinct units before any second question from a unit.
    for artifact in quizzes[:6]:
        if artifact.get("questions"):
            samples.append(sample("quiz", artifact["questions"][0], artifact["questions"][0]["knowledge_unit_id"]))
    counts = Counter(f["severity"] for f in findings)
    result = dict(schema_version="learning-run-audit-v1", run_id=run_id, manifest=str(manifest_path),
                  manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(), inventory=inventory,
                  provider=manifest.get("provider_metadata", {}).get("provider"), database_run=database_run,
                  database_latest_manifest=database_latest_manifest,
                  counts=dict(source_blocks=len(blocks), knowledge_units=len(units), tasks=len(tasks),
                              explanations=len(explanations), quiz_artifacts=len(quizzes), questions=len(questions),
                              sections=len(document.get("sections", []))), stages=stages,
                  review_status=review.get("status"), review_issue_counts=review.get("issue_counts", {}),
                  fact_check_summary=payloads.get("fact_check", {}).get("summary", {}),
                  finding_counts=dict(counts), findings=findings,
                  structural_status="blocked" if counts["blocking"] else "passed",
                  release_verdict="pending_manual_review" if not counts["blocking"] else "blocked")
    return result, dict(run_id=run_id, review_kind="preparation_only", samples=samples)


def model_usage(log: Path, run_id: str) -> dict:
    agents: Counter = Counter()
    statuses: Counter = Counter()
    usage: Counter = Counter()
    duration_ms = 0
    malformed_lines = 0
    with log.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                malformed_lines += 1
                continue
            if entry.get("metadata", {}).get("run_id") != run_id:
                continue
            agents[entry.get("metadata", {}).get("agent", "unknown")] += 1
            statuses[entry.get("status", "unknown")] += 1
            for key, value in entry.get("usage", {}).items():
                if isinstance(value, (int, float)):
                    usage[key] += value
            duration_ms += entry.get("duration_ms", 0) or 0
    return dict(scope="matching_run_id_only; log may be incomplete", calls=sum(agents.values()),
                agents=dict(agents), statuses=dict(statuses), usage=dict(usage),
                summed_call_duration_ms=duration_ms, malformed_lines=malformed_lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--model-log", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result, samples = audit_run(args.manifest, args.database)
    if args.model_log:
        result["model_log"] = model_usage(args.model_log, result["run_id"])
    # Exclusive directory creation prevents replacing previous audit evidence.
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for name, data in (("summary.json", result), ("samples.json", samples)):
        (args.output_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# 单章运行离线复评：{result['run_id']}", "", "仅检查已有产物，不调用模型或改变发布状态。", "",
             f"结构检查：`{result['structural_status']}`；发布判断：`{result['release_verdict']}`。", "",
             "```json", json.dumps(result["counts"], ensure_ascii=False, indent=2), "```", "",
             "完整发现见 summary.json；人工抽样正文、题目和原文证据见 samples.json。", ""]
    for finding in result["findings"][:40]:
        lines.append(f"- [{finding['severity']}] `{finding['code']}` · {finding['target']}：{finding['detail']}")
    (args.output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(dict(run_id=result["run_id"], counts=result["counts"], findings=result["finding_counts"],
                          structural_status=result["structural_status"], output=str(args.output_dir)), ensure_ascii=False))
    return 1 if result["structural_status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
