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
