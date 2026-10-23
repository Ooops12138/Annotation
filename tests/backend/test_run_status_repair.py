from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from annotation.api.persistence_service import persist_workflow_result
from annotation.persistence import PersistenceRepository
from test_persistence_service import _state

spec = importlib.util.spec_from_file_location("repair_run_status", Path(__file__).parents[2] / "scripts/repair_run_status.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_repair_is_explicit_backed_up_and_preserves_artifact(tmp_path):
    database = tmp_path / "db.sqlite3"
    repo = PersistenceRepository(database)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    book = repo.register_book(pdf)
    state = _state(tmp_path)
    repo.create_run(book["book_id"], run_id=state["run_id"])
    persist_workflow_result(repo, book=book, state=state)
    repo.close()
    artifact_path = Path(state["document_artifact_path"])
    original = artifact_path.read_bytes()
    manifest = tmp_path / "stopped.json"
    manifest.write_text(json.dumps(dict(run_id=state["run_id"], paths=dict(document=None, review=None),
                                      content_loop=dict(final_status="blocked"))), encoding="utf-8")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE artifacts SET path=? WHERE kind='run'", (str(manifest.resolve()),))
    output = tmp_path / "repair"
    plan = module.repair_run(manifest, database, output)
    assert plan["old_run"]["status"] == "succeeded"
    assert plan["new_status"] == "blocked" and not output.exists()
    receipt = module.repair_run(manifest, database, output, apply=True)
    assert receipt["applied"]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM runs").fetchone()[0] == "blocked"
        assert connection.execute("SELECT document_status FROM document_versions").fetchone()[0] == "blocked"
    with sqlite3.connect(output / "before.sqlite3") as connection:
        assert connection.execute("SELECT status FROM runs").fetchone()[0] == "succeeded"
    assert artifact_path.read_bytes() == original
    with pytest.raises(FileExistsError):
        module.repair_run(manifest, database, output, apply=True)
    stale = tmp_path / "stale.json"
    stale.write_bytes(manifest.read_bytes())
    with pytest.raises(ValueError, match="Stale manifest"):
        module.repair_run(stale, database, tmp_path / "stale-repair", apply=True)
    assert not (tmp_path / "stale-repair").exists()


def test_repair_refuses_manifest_without_evidence_of_a_stopped_run(tmp_path):
    manifest = tmp_path / "complete.json"
    manifest.write_text(json.dumps(dict(run_id="complete", paths=dict(document="doc.json", review="review.json"),
                                       interactive_component_summary=dict(final_status="failed"))))
    with pytest.raises(ValueError, match="No supported repair"):
        module.repair_run(manifest, tmp_path / "db.sqlite3", tmp_path / "repair", apply=True)
