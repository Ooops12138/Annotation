"""Recovery, resource ownership and optional-output regression tests."""

from collections import Counter
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier, Lock
import uuid

import pytest

from annotation.domain.artifacts import (
    InteractiveComponentSandboxReport,
    LearningDocument,
    LearningBlueprint,
)
from annotation.offline import OfflineFixtureProvider
from annotation.workflow import run_minimal_workflow
from annotation.workflow import (
    blueprint_nodes,
    content_nodes,
    review_nodes,
)
from annotation.workflow.checkpoints import (
    ChannelPayloadSerializer,
    resumable_checkpoint,
    workflow_checkpointer,
)
from annotation.workflow.context import RunResources, WorkflowContext
from annotation.workflow.control import RunControl, RunStopped
from annotation.workflow.quiz import blocked_quiz_artifact, generate_quiz_artifact
from test_content_reflection_subgraph import _state
from test_blueprint_loop import _install_source_fixture


def sandbox(*args, **kwargs):
    return InteractiveComponentSandboxReport(status="passed")


@pytest.mark.parametrize(
    "stage",
    [
        "prepare_content_tools",
        "content_reflection_loops",
        "generate_quiz_artifacts",
        "assemble",
    ],
)
def test_native_resume_rebuilds_resources_without_repeating_completed_nodes(
    monkeypatch, tmp_path, stage
):
    _install_source_fixture(monkeypatch)
    control = RunControl(f"resume-{uuid.uuid4().hex[:8]}", root=tmp_path)
    completed = Counter()

    def progress(name, state, phase):
        if phase == "completed":
            completed[name] += 1
            if name == stage:
                control.request_stop()

    with pytest.raises(RunStopped):
        run_minimal_workflow(
            provider=OfflineFixtureProvider(),
            run_id=control.run_id,
            run_control=control,
            progress_callback=progress,
            interactive_component_sandbox_runner=sandbox,
        )
    descriptor = resumable_checkpoint(control.run_id, tmp_path)
    assert descriptor is not None
    assert not (tmp_path / control.run_id / "latest.pkl").exists()
    with workflow_checkpointer(control.run_id, tmp_path) as saver:
        saved = saver.get_tuple(descriptor["config"])
        assert "content_retrieval_connection" not in saved.checkpoint["channel_values"]
    result = run_minimal_workflow(
        provider=OfflineFixtureProvider(),
        run_id=control.run_id,
        run_control=RunControl(control.run_id, root=tmp_path),
        resume_checkpoint=descriptor,
        progress_callback=lambda name, state, phase: (
            completed.update([name]) if phase == "completed" else None
        ),
        interactive_component_sandbox_runner=sandbox,
    )
    assert result["document"].run_id == control.run_id
    assert result["document"].status in {"accepted", "published"}
    assert (
        completed[stage] == completed["ingest"] == completed["plan_content_tasks"] == 1
    )
    assert all(count == 1 for count in completed.values())
    assert "content_retrieval_connection" not in result


def test_native_checkpoint_survives_a_new_python_process(monkeypatch, tmp_path):
    _install_source_fixture(monkeypatch)
    control = RunControl("process-resume", root=tmp_path)

    def progress(name, state, phase):
        if name == "prepare_content_tools" and phase == "completed":
            control.request_stop()

    with pytest.raises(RunStopped):
        run_minimal_workflow(
            provider=OfflineFixtureProvider(),
            run_id=control.run_id,
            run_control=control,
            progress_callback=progress,
            interactive_component_sandbox_runner=sandbox,
        )
    script = """
import json, sys
from pathlib import Path
from annotation.offline import OfflineFixtureProvider
from annotation.domain.artifacts import InteractiveComponentSandboxReport
from annotation.workflow import run_minimal_workflow, blueprint_nodes
from annotation.workflow.checkpoints import resumable_checkpoint
from annotation.workflow.control import RunControl
def no_ingest(*args, **kwargs):
    raise AssertionError('completed ingestion must not repeat')
blueprint_nodes.parse_pdf = no_ingest
root = Path(sys.argv[1])
control = RunControl('process-resume', root=root)
state = run_minimal_workflow(provider=OfflineFixtureProvider(), run_id=control.run_id,
    run_control=control, resume_checkpoint=resumable_checkpoint(control.run_id, root),
    interactive_component_sandbox_runner=lambda *a, **kw: InteractiveComponentSandboxReport(status='passed'))
print(json.dumps({'status': state['document'].status, 'source_count': len(state['source_blocks'])}))
"""
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        "RAG_ENABLED": "false",
    }
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["source_count"] == 3


def test_shared_retrieval_allows_concurrent_web_and_constructs_clients_once(
    monkeypatch,
):
    from annotation.workflow import context

    barrier = Barrier(2)
    builds = Counter()

    class Web:
        def search(self, query, **kwargs):
            barrier.wait(timeout=5)
            return query

    def web():
        builds["web"] += 1
        return Web()

    monkeypatch.setattr(context, "web_search_skill_from_env", web)
    monkeypatch.setenv("RAG_ENABLED", "false")
    from annotation.workflow.concurrency import ordered_batches

    with RunResources() as resources:
        service = resources.retrieval([])
        assert resources.retrieval([]) is service
        assert ordered_batches(
            lambda query: service.search(query, source="web"), ["one", "two"], workers=2
        ) == ["one", "two"]
        assert resources.web() is service.web
        connection = resources.connection
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("select 1")
    assert builds == {"web": 1}


def test_optional_quiz_failure_retains_diagnostics_and_keeps_body_readable(
    monkeypatch, tmp_path
):
    _install_source_fixture(monkeypatch)

    def failed_quiz(provider, **kwargs):
        return blocked_quiz_artifact(
            task=kwargs["task"],
            unit=kwargs["unit"],
            context_pack=kwargs["context_pack"],
            error="quiz provider unavailable",
            raw_response="invalid quiz payload",
        )

    monkeypatch.setattr(content_nodes, "generate_quiz_artifact", failed_quiz)
    run_id = f"optional-quiz-{uuid.uuid4().hex[:8]}"
    state = run_minimal_workflow(
        provider=OfflineFixtureProvider(),
        run_id=run_id,
        run_control=RunControl(run_id, root=tmp_path),
        interactive_component_sandbox_runner=sandbox,
    )
    assert all(
        artifact.status == "blocked" and artifact.raw_response
        for artifact in state["quiz_artifacts"]
    )
    assert state["quiz_coverage_report"].status == "blocked"
    assert state["review_report"].status != "blocked"
    assert state["document"].status in {"accepted", "published"}
    assert all(task.status == "accepted" for task in state["content_tasks"])
    assert not any(
        node.type == "quiz"
        for section in state["document"].sections
        for node in section.children
    )
    assert not state.get("errors")
    assert json.loads(Path(state["quiz_artifact_path"]).read_text(encoding="utf-8"))


def test_explicit_zero_quiz_calls_neither_provider_nor_retrieval():
    state = _state()

    class NoCalls:
        def __getattr__(self, name):
            raise AssertionError(f"zero quizzes must not call {name}")

    task = state["task"].model_copy(update={"quiz_count": 0})
    artifact = generate_quiz_artifact(
        OfflineFixtureProvider(),
        task=task,
        unit=state["unit"],
        context_pack=state["context_pack"],
        retrieval_service=NoCalls(),
    )
    assert artifact.status == "accepted" and artifact.questions == []
    assert artifact.generation_metadata["stop_reason"] == "not_requested"


def test_quiz_retrieval_persists_original_sources_and_updated_evidence(monkeypatch):
    from types import SimpleNamespace
    from annotation.domain.artifacts import (
        QuizDraft,
        SourceBlock,
    )
    from annotation.workflow.models import QuizRetrievalDecision
    from test_quiz import _pack, _question, _task, _unit

    unit = _unit()
    task, pack = _task(unit), _pack(unit)
    before = pack.model_dump()
    provider = OfflineFixtureProvider()
    monkeypatch.setattr(
        provider.workflow,
        "quiz_retrieval_decision",
        lambda: QuizRetrievalDecision(action="search", textbook_queries=["补充证据"]),
    )
    monkeypatch.setattr(
        provider.workflow,
        "quiz_draft",
        lambda unit, pack, count: QuizDraft(
            knowledge_unit_id=unit.artifact_id,
            target_objectives=unit.objective_ids,
            questions=[_question(unit, "extra-q", source_refs=["src-2"])],
        ),
    )
    block = SourceBlock(
        artifact_id="semantic-extra",
        status="accepted",
        run_id=task.run_id,
        version=1,
        created_by="test",
        source_ref="semantic-extra",
        source_refs=["src-2", "src-3"],
        document_id="doc",
        page_number=2,
        block_index=0,
        text="补充教材证据",
        text_hash="hash",
        parser_version="test",
        bbox=(0, 0, 1, 1),
        run_metadata={"chunk_type": "semantic"},
    )
    search = SimpleNamespace(
        search=lambda *args, **kwargs: SimpleNamespace(
            evidence=[
                SimpleNamespace(
                    source_ref=block.source_ref, text=block.text, page_number=2
                )
            ],
            status="ok",
            metadata={"index_fingerprint": "shared-index"},
        )
    )
    monkeypatch.setattr(RunResources, "retrieval", lambda *args: search)
    blueprint = LearningBlueprint(
        artifact_id="bp-quiz",
        status="accepted",
        run_id=task.run_id,
        version=1,
        created_by="test",
        title="test",
        knowledge_units=[unit],
    )
    state = {
        "run_id": task.run_id,
        "blueprint": blueprint,
        "content_tasks": [task],
        "context_packs": [pack],
        "semantic_blocks": [block],
        "source_refs": ["src-1", "src-2", "src-3"],
    }
    with RunResources() as resources:
        update = content_nodes.generate_quiz_artifacts(
            WorkflowContext(provider=provider, resources=resources), state
        )
    updated = update["context_packs"][0]
    assert pack.model_dump() == before
    assert updated.excerpts[-1].chunk_ref == "semantic-extra"
    assert updated.excerpts[-1].source_refs == ["src-2", "src-3"]
    assert updated.retrieval_calls[-1].selected_source_refs == ["src-2", "src-3"]
    assert updated.retrieval_calls[-1].metadata["index_fingerprint"] == "shared-index"
    assert "semantic-extra" not in updated.source_refs
    assert update["quiz_artifacts"][0].status == "accepted", update["quiz_artifacts"][
        0
    ].model_dump()
    assert (
        review_nodes.publishable_quizzes({**state, **update})
        == update["quiz_artifacts"]
    )
    saved = json.loads(
        Path(update["content_artifact_path"]).read_text(encoding="utf-8")
    )
    assert saved["context_packs"][0]["source_refs"] == updated.source_refs


def test_nodes_return_copies_and_document_validation_preserves_errors(monkeypatch):
    state = _state()
    ctx = WorkflowContext(provider=OfflineFixtureProvider())
    blueprint = LearningBlueprint(
        artifact_id="bp-test",
        run_id=state["run_id"],
        version=1,
        status="draft",
        created_by="test",
        title="test",
        knowledge_units=[state["unit"]],
    )
    before = blueprint.model_dump()
    update = blueprint_nodes.check_blueprint_attempt(
        ctx,
        {"blueprint": blueprint, "blueprint_preloaded": True, "source_refs": ["src-1"]},
    )
    assert blueprint.model_dump() == before
    assert update["blueprint"] is not blueprint
    assert review_nodes.assemble_document_ir(ctx, {"errors": ["previous"]}) == {
        "workflow_status": "blocked",
        "errors": ["previous", "assemble_document_requires_accepted_artifacts"],
    }
    document = LearningDocument.model_construct(
        artifact_id="doc", document_id="doc", run_id="run", title="test", sections=None
    )
    invalid = review_nodes.validate_document_ir(
        ctx, {"document": document, "errors": ["previous"]}
    )
    assert invalid["errors"][0] == "previous"
    assert invalid["errors"][1].startswith("document_validation:")


def test_component_workers_overlap_and_return_task_order(monkeypatch):
    from annotation.workflow import interactive_components

    barrier = Barrier(2)
    active = peak = 0
    lock = Lock()

    def component(provider, *, tasks, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=5)
            if tasks[0].task_id.endswith("0"):
                raise RuntimeError("optional sandbox failed")
            return {
                "interactive_component_artifacts": [],
                "interactive_component_status": "not_needed",
                "interactive_component_summary": {"attempt_count": 0},
            }
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(interactive_components, "_run_single_component_loop", component)
    task = _state()["task"]
    tasks = [
        task.model_copy(
            update={"task_id": f"task-{i}", "interactive_component_policy": "required"}
        )
        for i in range(4)
    ]
    result = interactive_components.run_interactive_component_loop(
        OfflineFixtureProvider(),
        run_id="components",
        tasks=tasks,
        context_packs=[],
        content_artifacts=[],
        units=[],
        valid_source_refs=[],
        max_attempts=1,
        max_workers=2,
    )
    assert peak == 2
    assert [
        item["content_task_id"] for item in result["interactive_component_results"]
    ] == [task.task_id for task in tasks]
    assert result["interactive_component_summary"]["failed_count"] == 1


def test_checkpoints_reuse_large_unchanged_channels_and_detect_corrupt_payloads(
    tmp_path,
):
    serde = ChannelPayloadSerializer(tmp_path)
    source = "教材内容" * 100_000
    first = {
        "channel_versions": {"source_blocks": "v1"},
        "channel_values": {"source_blocks": source},
    }
    payload = serde.dumps_typed(first)
    blob = next(tmp_path.iterdir())
    assert len(payload[1]) < 1000
    second = {
        "channel_versions": {"source_blocks": "v1", "warnings": "v2"},
        "channel_values": {"source_blocks": source, "warnings": ["diagnostic"]},
    }
    second_payload = serde.dumps_typed(second)
    assert len(list(tmp_path.iterdir())) == 2
    restored = ChannelPayloadSerializer(tmp_path).loads_typed(second_payload)
    assert restored["channel_values"] == second["channel_values"]
    blob.write_bytes(b"damaged payload")
    with pytest.raises(ValueError, match="integrity mismatch"):
        serde.loads_typed(payload)


def test_content_worker_does_not_mutate_task_or_evidence():
    from types import SimpleNamespace
    from annotation.domain.artifacts import ContentUnitLoopTrace

    state = _state()
    state["semantic_blocks"] = []
    task, pack, unit = state["task"], state["context_pack"], state["unit"]
    blueprint = LearningBlueprint(
        artifact_id="bp",
        run_id=state["run_id"],
        version=1,
        status="accepted",
        created_by="test",
        title="test",
        knowledge_units=[unit],
    )
    before_task, before_pack = task.model_dump(), pack.model_dump()

    def invoke(value, **kwargs):
        value["context_pack"].excerpts.clear()
        trace = ContentUnitLoopTrace(
            trace_id="trace",
            run_id=state["run_id"],
            task_id=task.task_id,
            knowledge_unit_id=unit.artifact_id,
            max_attempts=3,
            attempts=[],
            final_status="accepted",
            final_attempt=1,
            stop_reason="accepted",
        )
        return {"content_loop_trace": trace, "context_pack": value["context_pack"]}

    batch = content_nodes.ContentGenerationContext(
        WorkflowContext(OfflineFixtureProvider()),
        state,
        blueprint,
        {unit.artifact_id: unit},
        {task.task_id: pack},
        SimpleNamespace(invoke=invoke),
    )
    result = content_nodes.generate_content_task(batch, task)
    assert task.model_dump() == before_task
    assert pack.model_dump() == before_pack
    assert result["task"].status == "accepted"


def test_resume_endpoint_reuses_run_id_and_rejects_duplicate_jobs(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient
    from annotation.api import routes
    from annotation.main import app
    from annotation.persistence import PersistenceRepository
    from types import SimpleNamespace

    database = tmp_path / "library.sqlite3"
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"fixture")
    repo = PersistenceRepository(database)
    book = repo.register_book(pdf)
    repo.create_run(book["book_id"], run_id="resume-api")
    repo.update_run("resume-api", status="cancelled", error="user_requested_stop")
    repo.close()
    jobs = []
    monkeypatch.setattr(routes, "repository", lambda: PersistenceRepository(database))
    monkeypatch.setattr(
        routes, "resumable_checkpoint", lambda run_id: {"run_id": run_id, "config": {}}
    )
    monkeypatch.setattr(
        routes, "_run_executor", SimpleNamespace(submit=lambda *args: jobs.append(args))
    )
    try:
        response = TestClient(app).post("/api/runs/resume-api/resume")
        assert response.status_code == 200
        assert response.json()["run_id"] == "resume-api"
        assert len(jobs) == 1
        resumed_repo = PersistenceRepository(database)
        resumed = resumed_repo.get_run("resume-api")
        resumed_repo.close()
        assert resumed["error"] is None and resumed["finished_at"] is None
        assert TestClient(app).post("/api/runs/resume-api/resume").status_code == 409
    finally:
        routes._run_controls.pop("resume-api", None)


def test_stop_after_tool_response_preserves_receipt_and_retries_only_interrupted_stage(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace
    from annotation.workflow.control import ControlledPort

    _install_source_fixture(monkeypatch)
    monkeypatch.setenv("WORKFLOW_MAX_WORKERS", "1")
    control = [RunControl("tool-stop", root=tmp_path)]
    calls = []
    completed = Counter()
    build = content_nodes.build_content_reflection_subgraph

    class Tool:
        def generate(self):
            calls.append("completed response")
            if len(calls) == 1:
                control[0].request_stop()
            return {"response": "record before stopping"}

    def build_with_tool(*args, **kwargs):
        graph = build(*args, **kwargs)

        def invoke(state, **kwargs):
            ControlledPort(Tool(), control[0]).generate()
            return graph.invoke(state, **kwargs)

        return SimpleNamespace(invoke=invoke)

    def progress(name, state, phase):
        if phase == "completed":
            completed[name] += 1

    monkeypatch.setattr(
        content_nodes, "build_content_reflection_subgraph", build_with_tool
    )
    with pytest.raises(RunStopped):
        run_minimal_workflow(
            provider=OfflineFixtureProvider(),
            run_id="tool-stop",
            run_control=control[0],
            progress_callback=progress,
            interactive_component_sandbox_runner=sandbox,
        )
    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "tool-stop").glob("*.json")
    ]
    assert sum(receipt["kind"] == "generate" for receipt in receipts) == 1
    assert completed["ingest"] == 1 and completed["content_reflection_loops"] == 0
    control[0] = RunControl("tool-stop", root=tmp_path)
    state = run_minimal_workflow(
        provider=OfflineFixtureProvider(),
        run_id="tool-stop",
        run_control=control[0],
        resume_checkpoint=resumable_checkpoint("tool-stop", tmp_path),
        progress_callback=progress,
        interactive_component_sandbox_runner=sandbox,
    )
    assert state["document"].status in {"accepted", "published"}
    assert completed["ingest"] == completed["content_reflection_loops"] == 1
    assert len(calls) == 1 + len(state["content_tasks"])
