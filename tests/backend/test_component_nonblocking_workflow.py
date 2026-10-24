"""Optional component failures must not discard accepted learning content."""

import json
import uuid
from pathlib import Path

import pytest

from annotation.api.persistence_service import load_run_payload, persist_workflow_result
from annotation.domain.artifacts import InteractiveComponentSandboxReport
from annotation.persistence import PersistenceRepository
from annotation.offline import OfflineFixtureProvider
from annotation.providers.models import ProviderError
from annotation.workflow import run_minimal_workflow
from annotation.workflow import runtime
from annotation.workflow.interactive_components import run_interactive_component_loop
from test_interactive_component_loop import ScriptedComponentProvider, _interval_draft


def test_all_required_units_are_attempted_and_one_failure_keeps_other_components():
    calls = []

    def sandbox(spec, **kwargs):
        calls.append(kwargs["task_id"])
        if len(calls) == 1:
            raise RuntimeError("first component browser failed")
        return InteractiveComponentSandboxReport(status="passed")

    state = run_minimal_workflow(
        provider=OfflineFixtureProvider(), run_id=f"test-multiple-components-{uuid.uuid4().hex[:8]}",
        interactive_component_max_attempts=1, interactive_component_sandbox_runner=sandbox,
    )
    required = [task for task in state["content_tasks"] if task.interactive_component_policy == "required"]
    assert len(required) > 1
    results = state["interactive_component_results"]
    assert [result["content_task_id"] for result in results] == [task.task_id for task in required]
    assert len(calls) == len(required)
    assert results[0]["interactive_component_status"] == "failed"
    assert all(result["interactive_component_status"] == "accepted" for result in results[1:])
    components = [node for section in state["document"].sections for node in section.children if node.type == "interactive_component"]
    assert len(components) == len(required) - 1
    assert not state.get("errors")
    assert state["review_report"].status != "blocked"
    diagnostic = json.loads(Path(state["interactive_component_artifact_path"]).read_text(encoding="utf-8"))
    assert len(diagnostic["unit_results"]) == len(required)
    assert diagnostic["summary"]["failed_count"] == 1
    assert diagnostic["summary"]["component_count"] == len(components)


@pytest.mark.parametrize("failure,expected", [
    ("browser_error", "failed"),
    ("assertion_failed", "blocked"),
    ("invalid_spec", "blocked"),
    ("provider_error", "failed"),
    ("unexpected_error", "failed"),
    ("passed", "accepted"),
])
def test_component_outcome_never_blocks_document_publication(monkeypatch, tmp_path, failure, expected):
    def component_loop(provider, **kwargs):
        if failure == "unexpected_error":
            raise RuntimeError("component crashed")
        if failure == "provider_error":
            provider = ScriptedComponentProvider([ProviderError("component model unavailable", category="authentication", retryable=False)] * len(kwargs["tasks"]))
        elif failure == "invalid_spec":
            provider = ScriptedComponentProvider([lambda request: _interval_draft(request, refs=["unknown-source"])] * len(kwargs["tasks"]))
        kwargs["sandbox_runner"] = lambda *_args, **_kwargs: InteractiveComponentSandboxReport(
            status=failure if failure in {"browser_error", "assertion_failed"} else "passed",
            error="component validation failed" if failure in {"browser_error", "assertion_failed"} else None,
        )
        return run_interactive_component_loop(provider, **kwargs)

    monkeypatch.setattr(runtime, "run_interactive_component_loop", component_loop)
    state = run_minimal_workflow(
        provider=OfflineFixtureProvider(), run_id=f"test-component-optional-{uuid.uuid4().hex[:8]}",
        interactive_component_max_attempts=1,
    )
    assert state["interactive_component_status"] == expected
    assert bool(state["interactive_component_artifacts"]) == (expected == "accepted")
    assert not state.get("errors")
    assert state["review_report"].status != "blocked"
    nodes = [node for section in state["document"].sections for node in section.children]
    assert any(node.type == "markdown" for node in nodes)
    assert any(node.type == "quiz" for node in nodes)
    assert any(node.type == "interactive_component" for node in nodes) == (expected == "accepted")
    diagnostic = json.loads(Path(state["interactive_component_artifact_path"]).read_text(encoding="utf-8"))
    assert diagnostic["status"] == expected
    if failure != "unexpected_error":
        assert all(result["interactive_component_loop_trace"]["attempts"] for result in diagnostic["unit_results"])
    else:
        assert "component crashed" in diagnostic["summary"]["error"]

    repo = PersistenceRepository(tmp_path / "library.sqlite3")
    try:
        book = repo.register_book(state["pdf_path"])
        repo.create_run(book["book_id"], run_id=state["run_id"])
        result = persist_workflow_result(repo, book=book, state=state)
        assert result["run"]["status"] == "succeeded"
        assert result["document_version"] is not None
        restored = load_run_payload(repo, state["run_id"])
        assert restored["document"]["sections"]
        assert restored["interactive_component_summary"]["final_status"] == expected
    finally:
        repo.close()
