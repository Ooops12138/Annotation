from fastapi.testclient import TestClient
import uuid

from annotation.main import app


client = TestClient(app)


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
    assert payload["run"]["provider"] == "mock"
    assert payload["document"]["blueprint_version"].startswith("bp-fixture-001:v1")
    assert len(payload["quiz_artifacts"]) == 6
    assert all("question_count" not in artifact for artifact in payload["quiz_artifacts"])
    assert payload["quiz_coverage"]["status"] == "passed"
    assert payload["quiz_artifact_path"]
    assert payload["review_report"]["report_id"] == payload["document"]["review_report_id"]
    assert payload["review_report"]["checks"]["source_traceability"] == "passed"


def test_run_metadata_reflects_configured_provider(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("MODEL_NAME", "fixture-model")
    response = client.get("/api/run-metadata")
    assert response.status_code == 200
    assert response.json()["provider"] == "mock"


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
