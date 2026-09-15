from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from annotation.api.persistence_service import load_run_payload, persist_workflow_result
from annotation.main import app
from annotation.persistence import PersistenceRepository
from annotation.providers import SequenceProvider
from annotation.workflow import run_minimal_workflow

from test_blueprint_loop import _payload, _install_source_fixture


def test_blocked_loop_keeps_previous_document_version_current(monkeypatch, tmp_path: Path) -> None:
    _install_source_fixture(monkeypatch)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"fixture pdf")
    repo = PersistenceRepository(tmp_path / "annotation.sqlite3")
    book = repo.register_book(pdf)

    accepted = run_minimal_workflow(
        provider=SequenceProvider([_payload()]),
        run_id="persist-accepted-loop",
        document_id="doc-stable-loop",
    )
    repo.create_run(book["book_id"], run_id=accepted["run_id"])
    persist_workflow_result(repo, book=book, state=accepted)
    before = repo.get_current_document_version("doc-stable-loop")
    assert before is not None

    blocked = run_minimal_workflow(
        provider=SequenceProvider([_payload(refs=[]), _payload(refs=[]), _payload(refs=[])]),
        run_id="persist-blocked-loop",
        document_id="doc-stable-loop",
    )
    repo.create_run(book["book_id"], run_id=blocked["run_id"])
    result = persist_workflow_result(repo, book=book, state=blocked)

    assert result["run"]["status"] == "blocked"
    assert result["document"] is None
    assert result["document_version"] is None
    assert repo.get_current_document_version("doc-stable-loop")["document_version_id"] == before["document_version_id"]
    loaded = load_run_payload(repo, blocked["run_id"])
    assert loaded and loaded["status"] == "blocked"
    assert loaded["blueprint_loop"]["attempt_count"] == 3
    assert loaded["blueprint_loop"]["trace_path"]
    repo.close()


def test_get_run_route_returns_loop_summary_and_trace_path(monkeypatch, tmp_path: Path) -> None:
    _install_source_fixture(monkeypatch)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"fixture pdf")
    database = tmp_path / "annotation.sqlite3"
    repo = PersistenceRepository(database)
    book = repo.register_book(pdf)
    run_id = "api-persistence-loop"
    state = run_minimal_workflow(
        provider=SequenceProvider([_payload(refs=[]), _payload()]),
        run_id=run_id,
        pdf_path=pdf,
    )
    repo.create_run(book["book_id"], run_id=run_id)
    persist_workflow_result(repo, book=book, state=state)
    repo.close()

    # The route opens and closes its own repository connection, so a factory
    # is enough to point this endpoint at the isolated test database.
    import annotation.api.routes as routes

    monkeypatch.setattr(routes, "repository", lambda: PersistenceRepository(database))
    response = TestClient(app).get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    loop = payload["blueprint_loop"]
    assert loop["attempt_count"] == 2
    assert loop["final_status"] == "accepted"
    assert loop["trace_path"] == state["blueprint_trace_path"]
    assert "attempts" not in loop
