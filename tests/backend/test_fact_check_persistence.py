from __future__ import annotations

import json
from pathlib import Path

import pytest

from annotation.api.persistence_service import load_run_payload, persist_workflow_result
from annotation.domain.artifacts import FactCheckArtifact
from annotation.persistence import PersistenceRepository
from annotation.workflow.persistence import write_fact_check_artifact, write_run_manifest

from test_persistence_service import _state


def _fact_check_artifact(run_id: str) -> dict[str, object]:
    return {
        "artifact_id": f"fact-check-{run_id}",
        "run_id": run_id,
        "version": 1,
        "status": "at_risk",
        "created_by": "fact-check-agent",
        "policy": {"max_correction_rounds": 2, "web_enabled": False},
        "unit_traces": [{"knowledge_unit_id": "unit-1", "claims": ["claim-1"]}],
        "issues": [{"issue_id": "fact-check-1", "severity": "warning"}],
        "summary": {
            "final_status": "at_risk",
            "claim_count": 1,
            "contradiction_count": 0,
            "warning_count": 1,
            "correction_round_count": 0,
        },
        "artifact_path": None,
    }


def _persist_fact_check_run(tmp_path) -> tuple[dict, dict[str, object], Path]:
    state = _state(tmp_path)
    artifact = _fact_check_artifact(state["run_id"])
    state.update(
        {
            "fact_check_artifact": artifact,
            "fact_check_summary": artifact["summary"],
            "fact_check_status": "at_risk",
        }
    )
    fact_check_path = write_fact_check_artifact(state, root=tmp_path / "fact-check")
    assert fact_check_path is not None
    assert artifact["artifact_path"] == str(fact_check_path.resolve())
    state["fact_check_artifact_path"] = str(fact_check_path.resolve())

    snapshot = json.loads(fact_check_path.read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == "fact-check-artifact-v1"
    assert snapshot["fact_check"]["artifact_id"] == artifact["artifact_id"]

    manifest_path = write_run_manifest(
        state,
        blueprint_path=Path(state["blueprint_artifact_path"]),
        document_path=Path(state["document_artifact_path"]),
        root=tmp_path / "runs-with-fact-check",
    )
    state["run_manifest_path"] = str(manifest_path.resolve())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "run-manifest-v3"
    assert manifest["paths"]["fact_check"] == str(fact_check_path.resolve())
    assert manifest["fact_check_summary"] == artifact["summary"]

    repo = PersistenceRepository(tmp_path / "annotation.sqlite3")
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    book = repo.register_book(pdf)
    repo.create_run(book["book_id"], run_id=state["run_id"])
    persist_workflow_result(repo, book=book, state=state)

    registered = repo.get_artifact(f"fact_check-{state['run_id']}-v1")
    assert registered is not None
    assert registered["status"] == "at_risk"
    loaded = load_run_payload(repo, state["run_id"])
    assert loaded is not None
    assert loaded["fact_check_summary"] == artifact["summary"]
    assert loaded["fact_check_artifact_path"] == str(fact_check_path.resolve())
    repo.close()
    return state, artifact, fact_check_path


def test_fact_check_snapshot_is_indexed_and_keeps_standard_run_payload_compact(tmp_path) -> None:
    state, artifact, fact_check_path = _persist_fact_check_run(tmp_path)

    repo = PersistenceRepository(tmp_path / "annotation.sqlite3")
    loaded = load_run_payload(repo, state["run_id"])
    repo.close()

    assert loaded is not None
    assert loaded["fact_check_summary"] == artifact["summary"]
    assert loaded["fact_check_artifact_path"] == str(fact_check_path.resolve())
    assert "unit_traces" not in loaded


def test_fact_check_snapshot_updates_pydantic_artifact_path(tmp_path) -> None:
    artifact = FactCheckArtifact(
        artifact_id="fact-check-pydantic",
        run_id="run-pydantic",
        version=1,
        status="accepted",
        created_by="test",
        policy={},
        summary={"final_status": "accepted", "claim_count": 0},
    )
    state = {
        "run_id": artifact.run_id,
        "fact_check_artifact": artifact,
        "fact_check_status": artifact.status,
    }

    path = write_fact_check_artifact(state, root=tmp_path)

    assert path is not None
    assert artifact.artifact_path == str(path.resolve())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["fact_check"]["artifact_path"] == artifact.artifact_path


def test_fact_check_audit_endpoint_exposes_full_trace(monkeypatch, tmp_path) -> None:
    try:
        import python_multipart  # noqa: F401
    except ImportError:
        pytest.importorskip("multipart")
    from fastapi.testclient import TestClient

    from annotation.main import app

    import annotation.api.routes as routes

    state, artifact, _ = _persist_fact_check_run(tmp_path)
    monkeypatch.setattr(routes, "repository", lambda: PersistenceRepository(tmp_path / "annotation.sqlite3"))
    client = TestClient(app)
    run_response = client.get(f"/api/runs/{state['run_id']}")
    assert run_response.status_code == 200
    assert run_response.json()["fact_check_summary"] == artifact["summary"]
    assert "unit_traces" not in run_response.json()

    audit_response = client.get(f"/api/runs/{state['run_id']}/fact-check")
    assert audit_response.status_code == 200
    assert audit_response.json()["summary"] == artifact["summary"]
    assert audit_response.json()["fact_check"]["unit_traces"] == artifact["unit_traces"]
