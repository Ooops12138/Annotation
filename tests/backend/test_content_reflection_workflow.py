from __future__ import annotations

import uuid
from typing import Any

from annotation.domain.artifacts import KnowledgeUnit, LearningBlueprint, SourceBlock, SourceDocument
from annotation.providers.models import (
    ProviderCapabilities,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)
from annotation.offline import OfflineFixtureProvider
from annotation.workflow import run_minimal_workflow


class WorkflowContentProvider:
    provider = "workflow-scripted"
    profile = "live"
    model = "workflow-scripted-model"
    base_url = None
    config_version = "test-v1"

    def __init__(self, *, mode: str, quiz_unit_ids: list[str], context_window: int = 12000) -> None:
        self.mode = mode
        self.quiz_unit_ids = list(quiz_unit_ids)
        self.calls: list[StructuredGenerationRequest[Any]] = []
        self.capabilities = ProviderCapabilities(
            supports_structured_output=True,
            context_window=context_window,
        )

    def generate_structured(self, request: StructuredGenerationRequest[Any]) -> StructuredGenerationResponse[Any]:
        self.calls.append(request)
        schema_name = request.schema.__name__
        if schema_name == "ContentTaskPlanDraft":
            payload = {
                "tasks": [
                    {
                        "title": f"测试单元 {index}",
                        "covered_objectives": {unit_id: [f"{unit_id}-o1"]},
                        "quiz_count": 0,
                        "depth_guidance": "standard",
                        "interactive_component_policy": "skip",
                        "acceptance_criteria": ["使用测试教材证据。"],
                    }
                    for index, unit_id in enumerate(self.quiz_unit_ids, start=1)
                ]
            }
        elif schema_name == "RetrievalDecision":
            payload = {"action": "search", "textbook_queries": ["测试教材证据"]} if 'estimated_input_tokens=0' in request.prompt else {"action": "write"}
        elif schema_name == "ContentDraft":
            payload: dict[str, Any] = {
                "title": "测试单元",
                "content": "## 讲解\n\n先解释定义，再用 $x^2$ 说明一个教材例子。",
                "callouts": [],
                "source_refs": ["unknown-source"] if self.mode == "blocking" else ["src-1"],
            }
        elif schema_name == "ContentCritiqueDraft":
            if self.mode == "blocking":
                raise AssertionError("hard-check failures must not call the Critic")
            payload = {
                "issues": [
                    {
                        "code": "clarity",
                        "message": "需要先解释符号含义。",
                    }
                ] if not any(call.schema.__name__ == "ContentCritiqueDraft" for call in self.calls[:-1]) else []
            }
        elif schema_name == "QuizDraft":
            payload = {
                "knowledge_unit_id": self.quiz_unit_ids.pop(0),
                "target_objectives": [],
                "questions": [],
            }
        elif schema_name == "InteractiveComponentDraft":
            payload = {
                "task_id": request.metadata["task_id"],
                "knowledge_unit_id": request.metadata["knowledge_unit_id"],
                "context_pack_id": request.metadata["context_pack_id"],
                "spec": None,
                "not_needed_reason": "内容反思测试不生成交互组件。",
            }
        elif schema_name == "InteractiveComponentCritiqueDraft":
            payload = {"issues": []}
        else:  # pragma: no cover - makes a new workflow call visible in the test.
            raise AssertionError(f"unexpected schema: {schema_name}")
        value = request.schema.model_validate(payload)
        return StructuredGenerationResponse(
            value=value,
            raw_text=value.model_dump_json(),
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            config_version=self.config_version,
            duration_ms=0,
            usage={},
        )


def _install_source_fixture(monkeypatch) -> None:
    import annotation.workflow.blueprint_nodes as graph_module

    def parse_fixture(_path, *, run_id: str):
        document = SourceDocument(
            artifact_id=f"source-document-{run_id}",
            run_id=run_id,
            version=1,
            status="draft",
            source_refs=["src-1"],
            created_by="test",
            title="测试教材",
            locator="fixture://textbook",
        )
        block = SourceBlock(
            artifact_id=f"src-1-{run_id}",
            source_ref="src-1",
            document_id=document.artifact_id,
            run_id=run_id,
            version=1,
            status="draft",
            source_refs=["src-1"],
            created_by="test",
            page_number=1,
            block_index=0,
            text="测试教材证据：概念、定理与例题。",
            text_hash="source-hash",
            parser_version="fixture",
            bbox=(0, 0, 1, 1),
        )
        return document, [block]

    monkeypatch.setattr(graph_module, "parse_pdf", parse_fixture)
    monkeypatch.setattr(graph_module, "extraction_warnings", lambda _blocks: [])


def _blueprint(run_id: str) -> LearningBlueprint:
    units = [
        KnowledgeUnit(
            artifact_id=f"unit-{index}",
            run_id=run_id,
            version=1,
            status="accepted",
            source_refs=["src-1"],
            created_by="test",
            title=title,
            kind=kind,
            learning_objectives=[f"理解{title}"],
        )
        for index, (title, kind) in enumerate(
            (("概念", "concept"), ("定理", "theorem"), ("例题", "example")),
            start=1,
        )
    ]
    return LearningBlueprint(
        artifact_id=f"blueprint-{run_id}",
        run_id=run_id,
        version=1,
        status="accepted",
        source_refs=["src-1"],
        created_by="test",
        title="测试章节",
        knowledge_units=units,
    )


def test_critic_warning_reaches_review_report_and_keeps_accepted_preview(monkeypatch) -> None:
    _install_source_fixture(monkeypatch)
    run_id = f"content-warning-{uuid.uuid4().hex[:8]}"
    blueprint = _blueprint(run_id)
    provider = WorkflowContentProvider(
        mode="warning",
        quiz_unit_ids=[unit.artifact_id for unit in blueprint.knowledge_units],
    )

    state = run_minimal_workflow(provider=provider, run_id=run_id, blueprint=blueprint)

    assert state["workflow_status"] == "accepted"
    assert state["content_loop_status"] == "accepted"
    assert len(state["content_loop_traces"]) == 3
    assert all(trace.final_status == "accepted" for trace in state["content_loop_traces"])
    assert any(
        issue.message == "需要先解释符号含义。" and issue.severity == "warning"
        for issue in state["review_report"].issues
    )
    assert state["review_report"].status == "at_risk"
    assert state["document"].status == "accepted"


def test_exhausted_content_loop_blocks_run_and_skips_quiz_and_document(monkeypatch) -> None:
    _install_source_fixture(monkeypatch)
    run_id = f"content-blocked-{uuid.uuid4().hex[:8]}"
    blueprint = _blueprint(run_id)
    provider = WorkflowContentProvider(
        mode="blocking",
        quiz_unit_ids=[unit.artifact_id for unit in blueprint.knowledge_units],
    )

    state = run_minimal_workflow(
        provider=provider,
        run_id=run_id,
        blueprint=blueprint,
        content_reflection_max_attempts=3,
    )

    assert state["workflow_status"] == "blocked"
    assert state["content_loop_status"] == "blocked"
    assert "document" not in state
    assert "quiz_artifacts" not in state
    assert len(state["content_loop_traces"]) == 3
    assert all(trace.final_status == "blocked" for trace in state["content_loop_traces"])
    assert all(len(trace.attempts) == 3 for trace in state["content_loop_traces"])
    assert all(attempt.critic_status == "skipped" for trace in state["content_loop_traces"] for attempt in trace.attempts)
    assert all(call.schema.__name__ in {"ContentTaskPlanDraft", "ContentDraft", "RetrievalDecision"} for call in provider.calls)


def test_agent_cannot_write_when_retrieved_evidence_exceeds_budget(monkeypatch) -> None:
    _install_source_fixture(monkeypatch)
    import annotation.workflow.content_reflection as runtime
    original_context_pack = runtime.build_context_pack

    def tiny_content_budget(*args, **kwargs):
        kwargs["context_window"] = 1
        return original_context_pack(*args, **kwargs)

    # Keep the architect's request valid; only the content evidence budget is
    # exhausted. An impossible provider context now blocks at planning earlier.
    monkeypatch.setattr(runtime, "build_context_pack", tiny_content_budget)
    run_id = f"content-preflight-{uuid.uuid4().hex[:8]}"
    blueprint = _blueprint(run_id)
    provider = WorkflowContentProvider(
        mode="warning",
        quiz_unit_ids=[unit.artifact_id for unit in blueprint.knowledge_units],
        context_window=12000,
    )

    state = run_minimal_workflow(provider=provider, run_id=run_id, blueprint=blueprint)

    assert state["workflow_status"] == "blocked"
    assert state["content_loop_status"] == "blocked"
    assert not any(call.schema.__name__ == "ContentDraft" for call in provider.calls)
    assert all(trace.final_status == "blocked" for trace in state["content_loop_traces"])
    assert all(trace.final_attempt == 1 for trace in state["content_loop_traces"])
    assert all(trace.final_status == "blocked" for trace in state["content_loop_traces"])
    assert all(trace.attempts[0].generation_status == "provider_error" for trace in state["content_loop_traces"])


def test_offline_workflow_keeps_six_unit_quiz_regression_and_emits_six_content_traces() -> None:
    state = run_minimal_workflow(
        provider=OfflineFixtureProvider(),
        run_id=f"content-mock-regression-{uuid.uuid4().hex[:8]}",
    )

    assert len(state["blueprint"].knowledge_units) == 6
    assert len(state["content_loop_traces"]) == 6
    assert all(trace.final_status == "accepted" for trace in state["content_loop_traces"])
    assert all(len(trace.attempts) == 1 for trace in state["content_loop_traces"])
    assert all(trace.attempts[0].critic_status == "succeeded" for trace in state["content_loop_traces"])
    assert state["content_loop_summary"]["unit_count"] == 6
    assert state["content_loop_summary"]["critic_call_count"] == 6
    assert len(state["content_artifacts"]) == 6
    assert all("## 跟做材料" not in artifact.content for artifact in state["content_artifacts"])
    rendered_nodes = [node for section in state["document"].sections for node in section.children]
    assert {node.type for node in rendered_nodes}.issubset({"markdown", "callout", "quiz", "interactive_component"})
    assert any(node.type == "interactive_component" for node in rendered_nodes)
    assert not any(node.type == "callout" for node in rendered_nodes)
    assert len(state["quiz_artifacts"]) == 6


def test_enabled_vector_initialization_failure_keeps_workflow_fts(monkeypatch):
    _install_source_fixture(monkeypatch)
    import annotation.workflow.context as resources
    import annotation.workflow.content_nodes as runtime
    monkeypatch.setenv("RAG_ENABLED", "true")
    def broken_vector(*args, **kwargs):
        raise RuntimeError("index unavailable")
    monkeypatch.setattr(resources, "LlamaIndexChromaTextbookSearchTool", broken_vector)
    run_id = f"rag-degraded-{uuid.uuid4().hex[:8]}"
    state = run_minimal_workflow(provider=OfflineFixtureProvider(), run_id=run_id, blueprint=_blueprint(run_id))
    assert "vector_index_initialization_failed:RuntimeError" in state["warnings"]
    calls = [c for pack in state["context_packs"] for c in pack.retrieval_calls]
    assert calls
    assert any(c.metadata.get("degraded") and c.metadata.get("branch_1_status") == "error" for c in calls)
    assert state["content_artifacts"]


def test_enabled_vector_reaches_content_agent_and_context_audit(monkeypatch):
    _install_source_fixture(monkeypatch)
    import annotation.workflow.context as resources
    import annotation.workflow.content_nodes as runtime
    from annotation.fact_checking.contracts import EvidenceRecord, EvidenceKind, SearchResult, SearchStatus
    monkeypatch.setenv("RAG_ENABLED", "true")
    searched = []
    def make_vector(blocks, **kwargs):
        block = blocks[0]
        class Vector:
            name, version = "test-vector", "v1"
            def search(self, query, *, limit):
                searched.append(query)
                evidence = EvidenceRecord(evidence_id="e1", kind=EvidenceKind.TEXTBOOK, query=query,
                    text=block.text, excerpt=block.text, locator="page:1;block:0", provider=self.name,
                    source_ref=block.source_ref, document_id=block.document_id, page_number=1, block_index=0,
                    text_hash=block.text_hash)
                return SearchResult(skill_name=self.name, skill_version=self.version, query=query,
                                    status=SearchStatus.OK, evidence=(evidence,), metadata={"embedding_fingerprint": "test-vector-space"})
        return Vector()
    monkeypatch.setattr(resources, "LlamaIndexChromaTextbookSearchTool", make_vector)
    run_id = f"rag-enabled-{uuid.uuid4().hex[:8]}"
    state = run_minimal_workflow(provider=OfflineFixtureProvider(), run_id=run_id, blueprint=_blueprint(run_id))
    calls = [c for pack in state["context_packs"] for c in pack.retrieval_calls]
    assert searched and calls
    assert any(c.metadata.get("branch_1_embedding_fingerprint") == "test-vector-space" for c in calls)
    assert state["content_artifacts"]


def test_content_reuses_chunks_and_fts_across_units_and_runs(monkeypatch):
    import sqlite3
    import pytest
    import annotation.workflow.context as resources
    import annotation.workflow.content_nodes as runtime
    import annotation.workflow.content_reflection as reflection

    _install_source_fixture(monkeypatch)
    monkeypatch.setenv("RAG_ENABLED", "false")
    chunk_calls, indexed_connections, used_blocks = [], [], []
    chunker, indexer = runtime.build_semantic_blocks, resources.index_source_blocks
    context_builder = reflection.build_context_pack

    def chunks(blocks):
        result = chunker(blocks)
        chunk_calls.append(result)
        return result

    def index(connection, blocks):
        indexed_connections.append(connection)
        return indexer(connection, blocks)

    def context(*args, **kwargs):
        used_blocks.append(args[3])
        return context_builder(*args, **kwargs)

    def unexpected_chunking(_blocks):
        raise AssertionError("content subgraph must reuse the run's semantic blocks")

    monkeypatch.setattr(runtime, "build_semantic_blocks", chunks)
    monkeypatch.setattr(resources, "index_source_blocks", index)
    monkeypatch.setattr(reflection, "build_context_pack", context)
    monkeypatch.setattr(reflection, "build_semantic_blocks", unexpected_chunking)
    for _ in range(2):
        run_id = f"reuse-content-{uuid.uuid4().hex[:8]}"
        state = run_minimal_workflow(provider=OfflineFixtureProvider(), run_id=run_id, blueprint=_blueprint(run_id))
        assert len(state["content_artifacts"]) == 3
        assert "content_retrieval_connection" not in state
        assert used_blocks and all(blocks == chunk_calls[-1] for blocks in used_blocks)
        used_blocks.clear()
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            indexed_connections[-1].execute("SELECT 1")
    assert len(chunk_calls) == len(indexed_connections) == 2
    assert chunk_calls[0] is not chunk_calls[1]


def test_content_index_is_closed_when_tool_preparation_raises(monkeypatch):
    import sqlite3
    import pytest
    import annotation.workflow.context as resources
    import annotation.workflow.content_nodes as runtime

    _install_source_fixture(monkeypatch)
    connections = []
    indexer = resources.index_source_blocks

    def index(connection, blocks):
        connections.append(connection)
        return indexer(connection, blocks)

    def fail(*args, **kwargs):
        raise RuntimeError("context failed")

    monkeypatch.setattr(resources, "index_source_blocks", index)
    def failing_index(connection, blocks):
        connections.append(connection)
        fail()
    monkeypatch.setattr(resources, "index_source_blocks", failing_index)
    run_id = f"reuse-error-{uuid.uuid4().hex[:8]}"
    with pytest.raises(RuntimeError, match="context failed"):
        run_minimal_workflow(provider=OfflineFixtureProvider(), run_id=run_id, blueprint=_blueprint(run_id))
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_content_index_is_closed_when_reflection_raises(monkeypatch):
    import sqlite3
    import pytest
    import annotation.workflow.context as resources
    import annotation.workflow.content_nodes as runtime

    _install_source_fixture(monkeypatch)
    connections = []
    indexer = resources.index_source_blocks

    def index(connection, blocks):
        connections.append(connection)
        return indexer(connection, blocks)

    class BrokenSubgraph:
        def invoke(self, state, **kwargs):
            raise RuntimeError("reflection failed")

    monkeypatch.setattr(resources, "index_source_blocks", index)
    monkeypatch.setattr(runtime, "build_content_reflection_subgraph", lambda *args, **kwargs: BrokenSubgraph())
    run_id = f"reuse-reflection-error-{uuid.uuid4().hex[:8]}"
    with pytest.raises(RuntimeError, match="reflection failed"):
        run_minimal_workflow(provider=OfflineFixtureProvider(), run_id=run_id, blueprint=_blueprint(run_id))
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
