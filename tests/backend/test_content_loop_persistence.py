from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from annotation.api.persistence_service import load_run_payload, persist_workflow_result
from annotation.api.routes import _run_response_from_state
from annotation.config import content_reflection_max_attempts
from annotation.main import app
from annotation.persistence import PersistenceRepository
from annotation.workflow.content import write_content_run_artifact
from annotation.workflow.persistence import write_run_manifest

from test_persistence_service import _state


def _trace(status: str = "accepted") -> dict[str, object]:
    return {
        "trace_id": "content-loop-unit-1",
        "run_id": "run-service-test",
        "task_id": "task-1",
        "knowledge_unit_id": "unit-1",
        "context_pack_id": "ctx-task-1",
        "max_attempts": 3,
        "attempts": [{"attempt": 1, "route": status}],
        "final_status": status,
        "stop_reason": "accepted" if status == "accepted" else "attempts_exhausted",
    }


def test_content_artifact_v2_keeps_trace_path_and_summary(tmp_path) -> None:
    summary = {"unit_count": 1, "accepted_count": 1, "final_status": "accepted"}
    path = write_content_run_artifact(
        run_id="content-v2",
        blueprint_version="bp:v1",
        tasks=[],
        context_packs=[],
        artifacts=[],
        checks={},
        provider_metadata={"provider": "mock"},
        root=tmp_path,
        content_loop_traces=[_trace()],
        content_loop_summary=summary,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "content-artifact-v2"
    assert payload["content_loop_traces"] == [_trace()]
    assert payload["content_loop_summary"]["trace_path"] == str(path.resolve())
    assert summary["trace_path"] == str(path.resolve())

    derived_path = write_content_run_artifact(
        run_id="content-v2-derived",
        blueprint_version="bp:v1",
        tasks=[],
        context_packs=[],
        artifacts=[],
        checks={},
        provider_metadata={"provider": "mock"},
        root=tmp_path,
        content_loop_traces=[_trace()],
    )
    derived = json.loads(derived_path.read_text(encoding="utf-8"))["content_loop_summary"]
    assert derived["generation_call_count"] == 0
    assert derived["critic_call_count"] == 0
    assert derived["trace_path"] == str(derived_path.resolve())


def test_content_loop_persists_aggregate_status_and_loads_summary(tmp_path) -> None:
    repo = PersistenceRepository(tmp_path / "annotation.sqlite3")
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    book = repo.register_book(pdf)
    state = _state(tmp_path)
    trace = _trace("blocked")
    content_path = tmp_path / "content.json"
    content_path.write_text(
        json.dumps(
            {
                "schema_version": "content-artifact-v2",
                "content_artifacts": [],
                "content_loop_traces": [trace],
                "content_loop_summary": {
                    "unit_count": 1,
                    "accepted_count": 0,
                    "blocked_count": 1,
                    "failed_count": 0,
                    "attempt_count": 1,
                    "final_status": "blocked",
                },
            }
        ),
        encoding="utf-8",
    )
    state.update(
        {
            "content_loop_traces": [trace],
            "content_loop_summary": {
                "unit_count": 1,
                "accepted_count": 0,
                "blocked_count": 1,
                "failed_count": 0,
                "attempt_count": 1,
                "final_status": "blocked",
            },
            "content_loop_status": "blocked",
            "content_loop_trace_path": str(content_path),
            "workflow_status": "blocked",
        }
    )
    repo.create_run(book["book_id"], run_id=state["run_id"])

    result = persist_workflow_result(repo, book=book, state=state)
    loaded = load_run_payload(repo, state["run_id"])
    manifest_path = write_run_manifest(
        state,
        blueprint_path=None,
        document_path=None,
        root=tmp_path / "manifests",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert result["run"]["status"] == "blocked"
    assert result["document"] is None
    assert repo.get_artifact("content-run-service-test-v1")["status"] == "blocked"
    assert loaded and loaded["content_loop"] == {
        "unit_count": 1,
        "accepted_count": 0,
        "blocked_count": 1,
        "failed_count": 0,
        "attempt_count": 1,
        "final_status": "blocked",
        "trace_path": str(content_path),
    }
    assert manifest["schema_version"] == "run-manifest-v2"
    assert manifest["content_loop"]["final_status"] == "blocked"
    assert manifest["content_loop"]["trace_path"] == str(content_path)
    repo.close()


def test_load_run_payload_reconstructs_v2_summary_when_metadata_is_missing(tmp_path) -> None:
    repo = PersistenceRepository(tmp_path / "annotation.sqlite3")
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    book = repo.register_book(pdf)
    repo.create_run(book["book_id"], run_id="content-summary-fallback")
    content_path = tmp_path / "content.json"
    content_path.write_text(
        json.dumps(
            {
                "schema_version": "content-artifact-v2",
                "content_loop_traces": [_trace("accepted")],
            }
        ),
        encoding="utf-8",
    )
    repo.register_artifact("content-summary-fallback", "content", content_path, artifact_id="content-fallback")

    payload = load_run_payload(repo, "content-summary-fallback")

    assert payload and payload["content_loop"] == {
        "unit_count": 1,
        "accepted_count": 1,
        "blocked_count": 0,
        "failed_count": 0,
        "attempt_count": 1,
        "final_status": "accepted",
        "trace_path": str(content_path),
    }
    repo.close()


def test_run_endpoint_exposes_summary_without_full_content_trace(monkeypatch, tmp_path) -> None:
    repo = PersistenceRepository(tmp_path / "annotation.sqlite3")
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    book = repo.register_book(pdf)
    state = _state(tmp_path)
    trace = _trace()
    state.update(
        {
            "content_loop_traces": [trace],
            "content_loop_summary": {
                "unit_count": 1,
                "accepted_count": 1,
                "blocked_count": 0,
                "failed_count": 0,
                "attempt_count": 1,
                "final_status": "accepted",
            },
            "content_loop_status": "accepted",
            "content_loop_trace_path": state["content_artifact_path"],
        }
    )
    repo.create_run(book["book_id"], run_id=state["run_id"])
    result = persist_workflow_result(repo, book=book, state=state)
    repo.close()

    fresh_response = _run_response_from_state(result, state)
    assert fresh_response["content_loop"]["final_status"] == "accepted"
    assert "content_loop_traces" not in fresh_response

    import annotation.api.routes as routes

    monkeypatch.setattr(routes, "repository", lambda: PersistenceRepository(tmp_path / "annotation.sqlite3"))
    response = TestClient(app).get(f"/api/runs/{state['run_id']}")

    assert response.status_code == 200
    content_loop = response.json()["content_loop"]
    assert content_loop["attempt_count"] == 1
    assert content_loop["trace_path"] == state["content_artifact_path"]
    assert "content_loop_traces" not in content_loop


def test_content_reflection_attempt_limit_is_bounded(monkeypatch) -> None:
    monkeypatch.delenv("CONTENT_REFLECTION_MAX_ATTEMPTS", raising=False)
    assert content_reflection_max_attempts() == 3
    assert content_reflection_max_attempts(1) == 1
    assert content_reflection_max_attempts(7) == 3
    with pytest.raises(ValueError, match="CONTENT_REFLECTION_MAX_ATTEMPTS"):
        content_reflection_max_attempts(0)
    with pytest.raises(ValueError, match="CONTENT_REFLECTION_MAX_ATTEMPTS"):
        content_reflection_max_attempts("not-a-number")
