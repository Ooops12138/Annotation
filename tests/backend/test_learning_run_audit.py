"""Audit failures must be detected without modifying saved learning runs."""

import importlib.util
import json
from pathlib import Path
import sqlite3


spec = importlib.util.spec_from_file_location(
    "audit_learning_run", Path(__file__).parents[2] / "scripts/audit_learning_run.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def saved_run(tmp_path):
    def put(name, data):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    common = dict(run_id="r", source_refs=["s"])
    question = dict(question_id="q", knowledge_unit_id="u", target_objectives=["u-o1"],
                    question="Question", options=["yes", "no"], answer="yes", explanation="Because", source_refs=["s"])
    explanation = dict(common, artifact_id="c", task_id="t", context_pack_id="p",
                       knowledge_unit_ids=["u"], status="accepted", content="Explanation")
    quiz = dict(common, artifact_id="qz", task_id="t", context_pack_id="p", knowledge_unit_ids=["u"],
                status="accepted", question_count=1, questions=[question])
    paths = {
        "source_json": put("source", dict(blocks=[dict(common, source_ref="s", page_number=1, text="Source")])),
        "blueprint": put("blueprint", dict(blueprint=dict(common, artifact_id="b", version=1,
                            knowledge_units=[dict(common, artifact_id="u", title="Unit", learning_objectives=[dict(objective_id="u-o1", text="goal")])]))),
        "content": put("content", dict(common, blueprint_version="b:v1", tasks=[dict(common, task_id="t",
                           knowledge_unit_id="u", blueprint_version="b:v1", content_types=["explanation", "quiz"])],
                           context_packs=[dict(common, context_pack_id="p", task_id="t", knowledge_unit_id="u")],
                           content_artifacts=[explanation], quiz_artifacts=[quiz], quiz_coverage={})),
        "quiz": put("quiz", dict(common, quiz_artifacts=[quiz], quiz_coverage={})),
        "fact_check": put("fact", dict(common, status="accepted")),
        "interactive_components": put("interactive", dict(common, status="not_needed")),
        "document": put("document", dict(document=dict(common, document_id="d", review_report_id="rv", status="published",
                           sections=[dict(children=[dict(type="markdown", content="Explanation"), dict(question, id="q", type="quiz")])]))),
        "review": put("review", dict(report=dict(common, report_id="rv", document_id="d", status="passed"))),
    }
    manifest = Path(put("run", dict(run_id="r", paths=paths)))
    return manifest, paths


def mutate(path, change):
    path = Path(path)
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_valid_run_remains_pending_human_review_and_is_read_only(tmp_path):
    manifest, paths = saved_run(tmp_path)
    before = {p: Path(p).read_bytes() for p in paths.values()}
    result, samples = audit.audit_run(manifest)
    assert result["structural_status"] == "passed"
    assert result["release_verdict"] == "pending_manual_review"
    assert result["counts"]["questions"] == 1
    assert samples["samples"][0]["evidence"][0]["text"] == "Source"
    assert all(Path(p).read_bytes() == data for p, data in before.items())


def test_question_snapshot_and_projection_disagreement(tmp_path):
    manifest, paths = saved_run(tmp_path)
    mutate(paths["quiz"], lambda d: d["quiz_artifacts"][0].update(question_count=8))
    mutate(paths["document"], lambda d: d["document"]["sections"][0]["children"][1].update(answer="no"))
    result, _ = audit.audit_run(manifest)
    codes = {f["code"] for f in result["findings"]}
    assert {"quiz_snapshot_mismatch", "quiz_projection"} <= codes
    assert result["structural_status"] == "blocked"


def test_wrong_context_and_foreign_source(tmp_path):
    manifest, paths = saved_run(tmp_path)
    mutate(paths["content"], lambda d: d["content_artifacts"][0].update(context_pack_id="other", source_refs=["foreign"]))
    result, _ = audit.audit_run(manifest)
    codes = {f["code"] for f in result["findings"]}
    assert {"context_binding", "context_source", "unknown_source"} <= codes


def test_rejected_candidate_evidence_is_preserved_without_blocking_final_result(tmp_path):
    manifest, paths = saved_run(tmp_path)
    mutate(paths["content"], lambda d: d.update(content_loop_traces=[dict(attempts=[dict(source_refs=["rejected-source"])])]))
    result, _ = audit.audit_run(manifest)
    assert result["structural_status"] == "passed"


def test_unknown_renderer_node_is_blocking(tmp_path):
    manifest, paths = saved_run(tmp_path)
    mutate(paths["document"], lambda d: d["document"]["sections"][0]["children"].append(dict(type="untrusted")))
    result, _ = audit.audit_run(manifest)
    assert "unknown_node" in {f["code"] for f in result["findings"]}


def test_blocked_review_cannot_pass_structural_release_gate(tmp_path):
    manifest, paths = saved_run(tmp_path)
    mutate(paths["review"], lambda d: d["report"].update(status="blocked"))
    mutate(paths["document"], lambda d: d["document"].update(status="blocked"))
    result, _ = audit.audit_run(manifest)
    assert result["structural_status"] == "blocked"
    assert result["release_verdict"] == "blocked"


def test_blocked_component_is_a_warning_and_allows_database_success(tmp_path):
    manifest, paths = saved_run(tmp_path)
    mutate(paths["interactive_components"], lambda d: d.update(status="blocked"))
    db = tmp_path / "library.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE runs(run_id,status,provider,model,origin)")
        con.execute("INSERT INTO runs VALUES ('r','succeeded','test','test','test')")
    result, _ = audit.audit_run(manifest, db)
    assert result["structural_status"] == "passed"
    assert "database_status_conflict" not in {f["code"] for f in result["findings"]}
    assert next(f for f in result["findings"] if f["code"] == "stage_blocked")["severity"] == "warning"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT status FROM runs").fetchone()[0] == "succeeded"


def test_usage_reports_logged_calls_and_bad_lines(tmp_path):
    log = tmp_path / "calls.jsonl"
    entries = [dict(metadata=dict(run_id=run, agent="generate"), usage=dict(total_tokens=10), status="success", duration_ms=2)
               for run in ["r", "other"]]
    log.write_text("\n".join(json.dumps(e) for e in entries) + "\nbad-json\n")
    result = audit.model_usage(log, "r")
    assert result["calls"] == 1
    assert result["usage"]["total_tokens"] == 10
    assert result["malformed_lines"] == 1


def test_old_snapshot_does_not_contradict_current_database_success(tmp_path):
    manifest, paths = saved_run(tmp_path)
    mutate(paths["interactive_components"], lambda d: d.update(status="blocked"))
    database = tmp_path / "library.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE runs(run_id,status,provider,model,origin)")
        connection.execute("INSERT INTO runs VALUES ('r','succeeded','test','test','test')")
        connection.execute("CREATE TABLE artifacts(run_id,kind,version,path)")
        connection.execute("INSERT INTO artifacts VALUES ('r','run',2,?)", (str(tmp_path / 'run-revision-2.json'),))
    result, _ = audit.audit_run(manifest, database)
    codes = {f["code"] for f in result["findings"]}
    assert "historical_manifest" in codes
    assert "database_status_conflict" not in codes
    assert "stage_blocked" in codes
