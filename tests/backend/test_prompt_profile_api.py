from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from annotation.api import routes
from annotation.main import app
from annotation.offline import OfflineFixtureProvider
from annotation.persistence import PersistenceRepository
from annotation.workflow.control import RunStopped


@pytest.mark.parametrize("async_run", [False, True])
@pytest.mark.parametrize("profile", [None, "mathematics"])
def test_run_selection_reaches_worker_and_survives_stop(monkeypatch, tmp_path, async_run, profile):
    database = tmp_path / "library.sqlite3"
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"fixture")
    with_repo = PersistenceRepository(database)
    book = with_repo.register_book(pdf)
    with_repo.close()
    monkeypatch.setattr(routes, "repository", lambda: PersistenceRepository(database))
    monkeypatch.setattr(routes, "create_provider_from_env", OfflineFixtureProvider)
    jobs = []
    monkeypatch.setattr(routes, "_run_executor", SimpleNamespace(submit=lambda *args: jobs.append(args)))
    received = []

    def stop_workflow(**kwargs):
        received.append(kwargs["prompt_profile"])
        raise RunStopped("test_stop")

    monkeypatch.setattr(routes, "run_minimal_workflow", stop_workflow)
    run_id = "subject-api"
    try:
        response = TestClient(app).post("/api/runs", json={
            "book_id": book["book_id"], "run_id": run_id,
            "async_run": async_run, "prompt_profile": profile,
        })
        assert response.status_code == 200
        if async_run:
            assert len(jobs) == 1
            function, *args = jobs[0]
            function(*args)
        assert received == [profile]
        repo = PersistenceRepository(database)
        run = repo.get_run(run_id)
        repo.close()
        assert run["metadata"]["prompt_profile"] == profile
        assert run["status"] == "cancelled"
    finally:
        routes._run_controls.pop(run_id, None)


def test_unknown_selection_is_rejected_before_any_run_is_created(monkeypatch):
    def forbidden():
        raise AssertionError("repository/model work must not start")

    monkeypatch.setattr(routes, "repository", forbidden)
    response = TestClient(app).post("/api/runs", json={
        "book_id": "book", "prompt_profile": "unknown", "async_run": True,
    })
    assert response.status_code == 422
    assert "not found" in response.json()["detail"]


def test_subject_list_is_read_only_and_discovers_custom_packages(monkeypatch, tmp_path):
    import json
    import annotation.prompt_loader as loader

    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 2, "profiles": {"custom": {"label": "Custom", "slots": {}}},
    }), encoding="utf-8")
    monkeypatch.setattr(loader, "_PROMPT_LAYERS_DIR", tmp_path)
    response = TestClient(app).get("/api/prompt-profiles")
    assert response.status_code == 200
    assert response.json() == {"profiles": [{"id": "custom", "label": "Custom"}]}
