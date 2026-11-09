"""Efficiency changes must preserve evidence and native recovery guarantees."""
from collections import Counter
import uuid

import pytest

from annotation.offline import OfflineFixtureProvider
from annotation.providers import ProviderError
from annotation.workflow import run_minimal_workflow, content_nodes
from annotation.workflow.checkpoints import resumable_checkpoint
from annotation.workflow.control import RunControl, RunStopped
from annotation.workflow.content import render_context_pack, expand_source_aliases
from annotation.workflow.content_reflection import build_content_reflection_subgraph
from test_content_reflection_subgraph import _state, _draft, ScriptedContentProvider
from test_blueprint_loop import _install_source_fixture
from test_workflow_runtime import sandbox
from test_content_reflection_workflow import _blueprint


def test_existing_evidence_uses_one_author_call_and_one_critic_call():
    from types import SimpleNamespace
    from annotation.domain.artifacts import RetrievalCall
    state = _state()
    old = RetrievalCall(call_id="old", tool_name="hybrid", tool_version="v1", query="old",
                        limit=1, status="error", error_code="historical-import-failure")
    state["context_pack"].retrieval_calls.append(old)
    provider = ScriptedContentProvider([{"action": "write", "draft": _draft(refs=["E1"])}, {"issues": []}])
    result = build_content_reflection_subgraph(provider, max_attempts=1,
        retrieval_service=SimpleNamespace(available_modes=["keyword"], web_enabled=False)).invoke(state)
    assert result["route"] == "accept"
    assert [c.schema.__name__ for c in provider.calls] == ["ContentAgentStep", "ContentCritiqueDraft"]
    assert "historical-import-failure" not in provider.calls[0].prompt
    assert result["context_pack"].retrieval_calls[0] == old
    assert '"source_refs": [\n    "E1"' in provider.calls[1].prompt


def test_full_prompt_budget_blocks_before_author_call():
    from types import SimpleNamespace
    from annotation.providers.models import ProviderCapabilities
    provider = ScriptedContentProvider([])
    provider.capabilities = ProviderCapabilities(supports_structured_output=True, context_window=100)
    result = build_content_reflection_subgraph(provider, max_attempts=1,
        retrieval_service=SimpleNamespace(available_modes=["keyword"], web_enabled=False)).invoke(_state())
    assert provider.calls == []
    assert result["content_loop_trace"].attempts[0].generation_error_category == "budget"


def test_failed_keyword_probe_stops_before_content_model(monkeypatch):
    import annotation.workflow.context as resources
    _install_source_fixture(monkeypatch)
    monkeypatch.setenv("RAG_ENABLED", "false")
    def broken_query(*args, **kwargs):
        raise RuntimeError("index query unavailable")
    monkeypatch.setattr(resources.SQLiteFts5TextbookDatabaseSearchSkill, "search", broken_query)
    class Provider(OfflineFixtureProvider):
        authors = 0
        def generate_structured(self, request):
            if request.schema.__name__ == "ContentAgentStep":
                self.authors += 1
            return super().generate_structured(request)
    provider = Provider()
    run_id = f"probe-failure-{uuid.uuid4().hex[:8]}"
    with pytest.raises(ProviderError) as caught:
        run_minimal_workflow(provider=provider, run_id=run_id, blueprint=_blueprint(run_id))
    assert caught.value.category == "retrieval_health"
    assert provider.authors == 0


@pytest.mark.parametrize("reference,accepted", [("E1", True), ("E999", False)])
def test_aliases_are_restored_before_hard_checks(reference, accepted):
    state = _state()
    state["max_attempts"] = 1
    draft = _draft(refs=[reference])
    draft["evidence_notes"] = [{"claim": "教材定义", "reason": "需确认", "source_refs": [reference]}]
    provider = ScriptedContentProvider([draft, {"issues": []}] if accepted else [draft])
    result = build_content_reflection_subgraph(provider, max_attempts=1).invoke(state)
    assert (result["route"] == "accept") == accepted
    if accepted:
        artifact = result["candidate_artifact"]
        assert artifact.source_refs == ["src-1"]
        assert artifact.metadata["evidence_notes"][0]["source_refs"] == ["src-1"]
    else:
        assert len(provider.calls) == 1  # Unknown aliases cannot reach the critic.


def test_compact_evidence_keeps_all_text_and_constituent_references():
    pack = _state()["context_pack"]
    refs = [f"pdf-sha256-{'abc123' * 12}-page-1-block-{i}" for i in range(10)]
    pack.excerpts[0].source_refs = refs
    pack.excerpts[0].chunk_ref = "semantic-chunk-123"
    before = pack.model_dump(mode="json")
    rendered = render_context_pack(pack, aliases=True)
    assert pack.excerpts[0].text in rendered
    assert "[E1 p1]" in rendered and refs[0] not in rendered
    assert expand_source_aliases(["E1", "E1"], pack) == refs
    assert pack.model_dump(mode="json") == before


def test_task_cache_invalidates_changes_and_rechecks_saved_sources(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from annotation.workflow import content_task_cache as cache
    from annotation.prompt_archive import read_artifact_json, write_artifact_json
    state = _state()
    task, unit = state["task"], state["unit"]
    provider = ScriptedContentProvider([_draft(refs=["src-1"]), {"issues": []}])
    generated = build_content_reflection_subgraph(provider, max_attempts=1).invoke(state)
    batch = SimpleNamespace(ctx=SimpleNamespace(provider=provider, content_max_attempts=1,
        run_control=RunControl(state["run_id"], root=tmp_path)), units={unit.artifact_id: unit},
        state={"source_refs": ["src-1"], "source_blocks": []})
    path = cache.location(batch, task)
    result = {"task": task, "artifacts": generated["final_artifacts"],
              "traces": [generated["content_loop_trace"]], "updated_packs": [state["context_pack"]]}
    cache.save(path, result)
    assert cache.load(batch, task, path) is not None
    monkeypatch.setattr(provider, "model", "changed")
    assert cache.location(batch, task) != path
    monkeypatch.undo()
    changed_task = task.model_copy(update={"depth_guidance": "detailed"})
    assert cache.location(batch, changed_task) != path
    batch.state["source_blocks"] = [SimpleNamespace(source_ref="src-1", text="changed",
                                                  text_hash="new", parser_version="v2")]
    assert cache.location(batch, task) != path
    data = read_artifact_json(path)
    data["result"]["artifacts"][0]["source_refs"] = ["unknown-source"]
    write_artifact_json(path, data)
    assert cache.load(batch, task, path) is None
    path.write_text("{broken", encoding="utf-8")
    assert cache.load(batch, task, path) is None


@pytest.mark.parametrize("failure", ["interrupt", "provider"])
def test_task_cache_resumes_partial_content_without_repeating_accepted_tasks(monkeypatch, tmp_path, failure):
    _install_source_fixture(monkeypatch)
    monkeypatch.setenv("RAG_ENABLED", "false")
    monkeypatch.setenv("WORKFLOW_MAX_WORKERS", "1")
    control = RunControl(f"task-cache-{uuid.uuid4().hex[:8]}", root=tmp_path)

    class Provider(OfflineFixtureProvider):
        def __init__(self):
            super().__init__()
            self.authors = Counter()
            self.fail = failure == "provider"

        def generate_structured(self, request):
            if request.schema.__name__ == "ContentAgentStep":
                task = request.metadata["task_id"]
                self.authors[task] += 1
                if self.fail and len(self.authors) == 2:
                    self.fail = False
                    raise ProviderError("temporary account failure", category="authentication")
            return super().generate_structured(request)

    provider = Provider()
    original = content_nodes.generate_content_task
    completed = []

    def generate(batch, task):
        result = original(batch, task)
        completed.append(task.task_id)
        if failure == "interrupt" and len(completed) == 1:
            control.request_stop()
        return result

    monkeypatch.setattr(content_nodes, "generate_content_task", generate)
    kwargs = dict(provider=provider, run_id=control.run_id, blueprint=_blueprint(control.run_id),
                  interactive_component_sandbox_runner=sandbox)
    if failure == "interrupt":
        with pytest.raises(RunStopped):
            run_minimal_workflow(**kwargs, run_control=control)
    else:
        first = run_minimal_workflow(**kwargs, run_control=control)
        assert first["content_loop_status"] == "failed"
    first_task = completed[0]
    calls = provider.authors[first_task]
    assert calls and list(tmp_path.rglob("content-tasks/*.json"))
    state = run_minimal_workflow(**kwargs, run_control=RunControl(control.run_id, root=tmp_path),
                                 resume_checkpoint=resumable_checkpoint(control.run_id, tmp_path))
    assert provider.authors[first_task] == calls
    assert state["document"].status in {"accepted", "published"}
    assert state["content_loop_summary"]["cache_hit_count"] >= 1
