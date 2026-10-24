from fastapi.testclient import TestClient
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from annotation.api import routes
from annotation.main import app


client = TestClient(app)


def test_async_run_completes_and_restores_offline_document(monkeypatch, tmp_path) -> None:
    from annotation.persistence import PersistenceRepository

    seed = client.get("/api/library").json()["books"][0]
    database = tmp_path / "library.sqlite3"
    repo = PersistenceRepository(database)
    book = repo.register_book(seed["stored_path"])
    repo.close()
    monkeypatch.setattr(routes, "repository", lambda: PersistenceRepository(database))
    monkeypatch.setenv("MODEL_PROFILE", "offline")
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    started, release = Event(), Event()
    workflow = routes.run_minimal_workflow

    def paused_workflow(**kwargs):
        callback = kwargs["progress_callback"]

        def publish(stage, state, phase):
            callback(stage, state, phase)
            if stage == "ingest" and phase == "started":
                started.set()
                assert release.wait(20)

        return workflow(**{**kwargs, "progress_callback": publish})

    monkeypatch.setattr(routes, "run_minimal_workflow", paused_workflow)
    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(routes, "_run_executor", executor)
        run_id = f"test-progress-{uuid.uuid4().hex[:8]}"
        try:
            response = client.post("/api/runs", json={"book_id": book["book_id"], "run_id": run_id, "async_run": True})
            assert response.status_code == 200
            assert started.wait(10)
            snapshot = client.get(f"/api/runs/{run_id}").json()
            assert snapshot["status"] == "running"
            assert snapshot["progress"]["stage"] == "ingest"
            assert snapshot["progress"]["status"] == "running"
            # Attaching to a running task must not submit another generation.
            attached = client.post("/api/runs", json={"book_id": book["book_id"], "run_id": run_id, "async_run": True}).json()
            assert attached["run_id"] == run_id
        finally:
            release.set()
    restored = client.get(f"/api/runs/{run_id}").json()
    assert restored["status"] == "succeeded"
    assert restored["document"]["sections"]
    assert restored["source_locations"]
    assert restored["progress"]["completed"] == restored["progress"]["total"]
    assert restored["progress"]["label"]
    repo = PersistenceRepository(database)
    try:
        assert len(repo.list_runs(book["book_id"])) == 1
        assert not repo.list_documents(book["book_id"])
    finally:
        repo.close()


def test_async_run_persists_initial_progress_snapshot(monkeypatch) -> None:
    class DeferredExecutor:
        def __init__(self) -> None:
            self.submitted = []

        def submit(self, function, *args) -> None:
            self.submitted.append((function, args))

    executor = DeferredExecutor()
    monkeypatch.setattr(routes, "_run_executor", executor)
    book_id = client.get("/api/library").json()["books"][0]["book_id"]
    run_id = f"test-async-{uuid.uuid4().hex[:8]}"

    started = client.post("/api/runs", json={"book_id": book_id, "run_id": run_id, "async_run": True})
    assert started.status_code == 200
    assert started.json()["progress"]["status"] == "queued"
    assert len(executor.submitted) == 1

    snapshot = client.get(f"/api/runs/{run_id}").json()
    assert snapshot["run_id"] == run_id
    assert snapshot["status"] == "running"
    assert snapshot["progress"]["label"] == "等待开始"


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_demo_document_contract() -> None:
    response = client.get("/api/demo-document")
    payload = response.json()
    nodes = [node for section in payload["sections"] for node in section["children"]]

    assert response.status_code == 200
    assert payload["issues"][0]["severity"] == "warning"
    assert len(payload["sections"]) >= 3
    assert {node["type"] for node in nodes} <= {"markdown", "callout", "quiz"}
    assert "formula" not in {node["type"] for node in nodes}
    assert "example" not in {node["type"] for node in nodes}
    assert not any(node["type"] == "callout" and node.get("title", "").startswith("例：") for node in nodes)
    assert any(node["type"] == "markdown" and node["id"].startswith("example-") for node in nodes)


def test_demo_document_renders_math_in_rich_node_text() -> None:
    response = client.get("/api/demo-document")
    nodes = [node for section in response.json()["sections"] for node in section["children"]]
    callout = next(node for node in nodes if node["type"] == "callout")
    examples = [node for node in nodes if node["type"] == "markdown" and node["id"].startswith("example-")]
    quiz = next(node for node in nodes if node["type"] == "quiz")
    formulas = [node for node in nodes if node["type"] == "markdown" and "$$" in node["content"]]

    assert "$" in callout["content"]
    assert formulas
    assert all("$" in example["content"] for example in examples)
    assert "$" in quiz["question"]
    assert all("$" in option for option in quiz["options"])
    assert "$" in quiz["answer"] and "$" in quiz["explanation"]


def test_minimal_workflow_endpoint_uses_offline_provider_by_default() -> None:
    books = client.get("/api/library").json()["books"]
    response = client.post("/api/runs", json={"book_id": books[0]["book_id"], "run_id": f"test-api-quiz-{uuid.uuid4().hex[:8]}"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["run"]["provider"] == "offline"
    assert payload["document"]["blueprint_version"].startswith("bp-fixture-001:v1")
    assert len(payload["quiz_artifacts"]) == 6
    assert all("question_count" not in artifact for artifact in payload["quiz_artifacts"])
    assert payload["quiz_coverage"]["status"] == "passed"
    assert payload["quiz_artifact_path"]
    assert payload["review_report"]["report_id"] == payload["document"]["review_report_id"]
    assert payload["review_report"]["checks"]["source_traceability"] == "passed"


def test_run_metadata_reflects_configured_provider(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROFILE", "offline")
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("MODEL_NAME", "offline-fixture")
    response = client.get("/api/run-metadata")
    assert response.status_code == 200
    assert response.json()["provider"] == "offline"


def test_library_read_does_not_construct_model_provider(monkeypatch) -> None:
    def fail_provider():
        raise AssertionError("library reads must not initialize a model provider")

    monkeypatch.setattr("annotation.api.routes.create_provider_from_env", fail_provider)
    response = client.get("/api/library")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_saved_document_read_does_not_construct_model_provider(monkeypatch, tmp_path) -> None:
    from annotation.persistence import PersistenceRepository
    from annotation.api.persistence_service import persist_workflow_result
    from test_persistence_service import _state
    database = tmp_path / "library.sqlite3"
    repo = PersistenceRepository(database)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    book = repo.register_book(pdf)
    state = _state(tmp_path)
    repo.create_run(book["book_id"], run_id=state["run_id"])
    persist_workflow_result(repo, book=book, state=state)
    repo.close()
    monkeypatch.setattr("annotation.api.routes.repository", lambda: PersistenceRepository(database))
    def fail_provider():
        raise AssertionError("document reads must not initialize a model provider")

    monkeypatch.setattr("annotation.api.routes.create_provider_from_env", fail_provider)
    document_id = state["document"].document_id
    response = client.get(f"/api/documents/{document_id}")
    assert response.status_code == 200
    assert response.json()["document"] is not None


def test_retired_workflow_route_is_not_available():
    assert client.post("/api/workflow/run").status_code == 404
