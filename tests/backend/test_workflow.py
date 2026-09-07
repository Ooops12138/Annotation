from annotation.providers import MockProvider
from annotation.workflow import run_minimal_workflow


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
