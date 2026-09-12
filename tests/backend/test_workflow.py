from annotation.providers import MockProvider
from annotation.workflow import run_minimal_workflow
from pathlib import Path


def test_minimal_langgraph_workflow_produces_traceable_document() -> None:
    state = run_minimal_workflow(provider=MockProvider(), run_id="run-test-001")
    assert not state.get("errors")
    assert state["blueprint"].run_id == "run-test-001"
    assert state["document"].run_id == "run-test-001"
    assert state["document"].status == "published"
    assert state["document"].blueprint_version.startswith("bp-fixture-001:v1")
    assert state["provider_metadata"]["provider"] == "mock"
    assert len(state["document"].source_refs) <= 5
    assert not state.get("warnings")
    assert len(state["blueprint"].knowledge_units) >= 4
    assert state["blueprint_check"].status == "accepted"
    assert len(state["document"].sections) >= 3
    assert sum(len(section.children) for section in state["document"].sections) >= 10
    assert len(state["content_tasks"]) == len(state["blueprint"].knowledge_units)
    assert len(state["context_packs"]) == len(state["content_tasks"])
    assert len(state["content_artifacts"]) >= len(state["content_tasks"])
    assert all(artifact.status == "accepted" for artifact in state["content_artifacts"])
    assert all(artifact.context_pack_id for artifact in state["content_artifacts"])
    assert any(artifact.material_role == "bridge" for artifact in state["content_artifacts"])
    assert all(task.status == "accepted" for task in state["content_tasks"])
    assert Path(state["content_artifact_path"]).is_file()
    assert state["review_report"].status in {"passed", "at_risk", "blocked"}
    assert state["review_report"].report_id == state["document"].review_report_id
    assert Path(state["review_report_path"]).is_file()


def test_review_report_version_is_incremented_for_a_repeated_run() -> None:
    first = run_minimal_workflow(provider=MockProvider(), run_id="run-review-version-test")
    second = run_minimal_workflow(provider=MockProvider(), run_id="run-review-version-test")
    assert second["review_report"].version > first["review_report"].version
    assert second["review_report_path"] != first["review_report_path"]
    assert second["review_report"].revision_of == first["review_report"].report_id
