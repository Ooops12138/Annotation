from __future__ import annotations

import uuid

from annotation.domain.artifacts import KnowledgeUnit, LearningBlueprint, SourceBlock, SourceDocument
from tests.support.providers import SequenceProvider
from annotation.workflow import run_minimal_workflow


def _install_source_fixture(monkeypatch) -> None:
    import annotation.workflow.blueprint_nodes as graph_module

    def parse_fixture(_path, *, run_id: str):
        document = SourceDocument(
            artifact_id=f"source-document-{run_id}",
            run_id=run_id,
            version=1,
            status="draft",
            source_refs=["src-1", "src-2", "src-3"],
            created_by="test",
            title="测试教材",
            locator="fixture://textbook",
        )
        blocks = [
            SourceBlock(
                artifact_id=f"{ref}-artifact",
                source_ref=ref,
                document_id=document.artifact_id,
                run_id=run_id,
                version=1,
                status="draft",
                source_refs=[ref],
                created_by="test",
                page_number=1,
                block_index=index,
                text=f"教材片段 {index}",
                text_hash=f"hash-{index}",
                parser_version="fixture",
                bbox=(0, 0, 1, 1),
            )
            for index, ref in enumerate(document.source_refs)
        ]
        return document, blocks

    monkeypatch.setattr(graph_module, "parse_pdf", parse_fixture)
    monkeypatch.setattr(graph_module, "extraction_warnings", lambda _blocks: [])


def _payload(*, refs: list[str] | None = None, warning: bool = False, unit_count: int = 3) -> dict:
    source_refs = ["src-1"] if refs is None else refs
    objective = [] if warning else ["理解教材内容"]
    templates = [
        {"title": "概念", "kind": "concept", "learning_objectives": objective},
        {"title": "定理", "kind": "theorem", "learning_objectives": ["理解定理"]},
        {"title": "例题", "kind": "example", "learning_objectives": ["完成例题"]},
    ]
    return {
        "title": "测试蓝图",
        "knowledge_units": [
            {**templates[index % len(templates)],
             "title": templates[index % len(templates)]["title"] if index < 3 else f"单元 {index + 1}",
             "source_refs": source_refs}
            for index in range(unit_count)
        ],
    }


def test_preloaded_blueprint_is_validated_without_blueprint_calls(monkeypatch):
    _install_source_fixture(monkeypatch)
    bp = LearningBlueprint(artifact_id="preloaded", run_id="preloaded", version=1,
        status="accepted", created_by="test", title="Book", knowledge_units=[KnowledgeUnit(
            artifact_id="unit", run_id="preloaded", version=1, status="accepted", created_by="test",
            title="Unit", kind="concept", learning_objectives=["Goal"])])
    provider = SequenceProvider([])
    state = run_minimal_workflow(provider=provider, run_id=f"preloaded-{uuid.uuid4().hex[:8]}", blueprint=bp)
    assert provider.calls == []
    assert state["blueprint_hierarchy_trace"].stop_reason == "preloaded"
    assert state["workflow_status"] == "accepted"


def test_single_window_uses_local_and_coordinator_nodes(monkeypatch):
    from annotation.offline import OfflineFixtureProvider
    _install_source_fixture(monkeypatch)
    nodes = []
    state = run_minimal_workflow(provider=OfflineFixtureProvider(), run_id=f"unified-{uuid.uuid4().hex[:8]}",
        progress_callback=lambda name, state, event: nodes.append(name) if event == "started" else None)
    assert state["workflow_status"] == "accepted"
    assert nodes[:3] == ["ingest", "prepare_blueprint", "hierarchical_blueprint"]
    trace = state["blueprint_hierarchy_trace"]
    assert len(trace.windows) == 1
    assert [call.phase for call in trace.calls] == ["generate_local_blueprint", "coordinate_blueprint"]
    assert "blueprint_loop_trace" not in state
