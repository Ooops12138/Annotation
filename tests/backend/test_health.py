from fastapi.testclient import TestClient

from annotation.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_demo_document_contract() -> None:
    response = client.get("/api/demo-document")
    payload = response.json()
    assert response.status_code == 200
    assert payload["sections"][0]["children"][1]["type"] == "formula"
    assert payload["issues"][0]["severity"] == "warning"


def test_minimal_workflow_endpoint_uses_offline_provider_by_default() -> None:
    response = client.post("/api/workflow/run")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["provider_metadata"]["provider"] == "mock"
    assert payload["document"]["blueprint_version"].startswith("bp-fixture-001:v1")
