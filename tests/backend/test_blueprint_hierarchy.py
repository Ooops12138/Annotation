from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from annotation.domain.artifacts import SourceBlock, SourceDocument
from annotation.ingestion.structure import build_textbook_structure
from annotation.providers import ProviderError
from annotation.providers.models import ProviderCapabilities, StructuredGenerationResponse
from annotation.workflow.blueprint_hierarchy import (
    BlueprintEngine, CoordinationDecision, LocalBlueprintDraft, build_blueprint_windows,
)
from annotation.workflow.budget import PromptBudget


def source(texts: list[str], *, run_id: str = "hierarchy-run", toc=None):
    document = SourceDocument(artifact_id="book-stable", run_id=run_id, version=1, status="draft",
        title="测试教材", created_by="test", locator="fixture://book", run_metadata={"toc": toc or []})
    blocks = [SourceBlock(artifact_id=f"block-{i}", source_ref=f"src-{i}", document_id=document.artifact_id,
        run_id=run_id, version=1, status="draft", created_by="test", page_number=i // 3 + 1,
        block_index=i, text=text, raw_text=text, text_hash=hashlib.sha256(text.encode()).hexdigest(),
        parser_version="test", bbox=(0, 0, 1, 1)) for i, text in enumerate(texts)]
    document.source_refs = [b.source_ref for b in blocks]
    return document, blocks


def section(prompt: str, label: str) -> Any:
    return json.loads(prompt.split(f"## {label}\n", 1)[1].split("\n\n##", 1)[0].strip())


class TeachingProvider:
    provider = "hierarchy-test"
    model = "offline-model"
    base_url = None
    config_version = "test-v1"
    capabilities = ProviderCapabilities(supports_structured_output=True, context_window=32000)

    def __init__(self, decide=None):
        self.calls = []
        self.decide = decide

    def generate_structured(self, request):
        self.calls.append(request)
        value = self.decide(request) if self.decide else None
        if isinstance(value, Exception):
            raise value
        if value is None and request.schema == LocalBlueprintDraft:
            window = section(request.prompt, "Location and source window")
            value = {"title": window["path"][-1], "summary": window["path"][-1],
                "representative_unit_ids": ["unit"], "knowledge_units": [{"knowledge_unit_id": "unit",
                    "title": window["path"][-1], "kind": "concept", "learning_objectives": ["理解本节内容"],
                    "source_refs": [window["owned"][0]["source_ref"]]}]}
        elif value is None and request.schema == CoordinationDecision:
            groups = section(request.prompt, "Groups")
            value = {"action": "finalize", "group_order": [g["group_id"] for g in groups],
                "summary": "按教材层级组织学习", "representative_unit_ids": [r["unit_id"] for g in groups for r in g["representatives"]][:6]}
        elif value is None:
            # Full-workflow tests stop at architect planning when desired.
            value = {"tasks": []}
        parsed = request.schema.model_validate(value)
        return StructuredGenerationResponse(value=parsed, raw_text=parsed.model_dump_json(),
            provider=self.provider, model=self.model, base_url=None, config_version=self.config_version,
            duration_ms=1, usage={"input_tokens": 20, "output_tokens": 10})


@pytest.fixture(autouse=True)
def isolate_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("BLUEPRINT_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setenv("BLUEPRINT_INPUT_BUDGET_TOKENS", "16000")
    monkeypatch.setenv("BLUEPRINT_CACHE_ENABLED", "true")


def engine_for(texts, provider=None, *, run_id="hierarchy-run", budget=None, attempts=3):
    document, blocks = source(texts, run_id=run_id)
    structure = build_textbook_structure(document, blocks)
    engine = BlueprintEngine(provider or TeachingProvider(), document, blocks, structure,
        max_attempts=attempts, budget=budget)
    windows = build_blueprint_windows(structure, blocks, engine.budget)
    return engine, windows, blocks


def test_structure_owns_every_block_once_with_same_page_boundaries_and_introduction():
    document, blocks = source(["前言", "第1章 实数", "章引言", "1.1 实数集", "正文", "1.2 确界", "正文", "第2章 极限", "正文"])
    tree = build_textbook_structure(document, blocks)
    assert [n.title for n in tree.nodes] == ["测试教材", "第1章 实数", "1.1 实数集", "1.2 确界", "第2章 极限"]
    owned = [i for n in tree.nodes for span in n.owned_ranges for i in range(span.start, span.end)]
    assert sorted(owned) == list(range(len(blocks)))
    assert tree.nodes[2].parent_id == tree.nodes[1].node_id
    assert tree.nodes[1].end == 7
    assert tree.nodes[1].owned_ranges[0].start == 1
    assert tree.nodes[1].owned_ranges[0].end == 3


def test_bookmark_matches_title_within_page_and_retains_empty_page_warnings():
    document, blocks = source(["前言", "Chapter 1 Numbers", "text", "Chapter 2 Limits", "text"],
        toc=[[1, "Chapter 1 Numbers", 1], [1, "Chapter 2 Limits", 2], [2, "Bad", 9]])
    document.run_metadata["empty_text_pages"] = [9]
    tree = build_textbook_structure(document, blocks)
    assert tree.nodes[1].start == 1
    assert tree.nodes[1].origin == "bookmark"
    assert any("bookmark_without_text:9" in w for w in tree.warnings)
    assert "page_without_extracted_text:9" in tree.warnings


def test_large_source_blocks_are_lossless_and_each_request_reserves_schema_output():
    text = "开头" + "定义与条件。" * 3000 + "末尾不可截断"
    engine, windows, blocks = engine_for([text])
    assert len(windows) > 1
    slices = [s for w in windows for s in w.slices]
    assert "".join(blocks[0].text[s.start:s.end] for s in slices) == text
    assert all(a.end == b.start for a, b in zip(slices, slices[1:]))
    blueprint = engine.run(windows)
    assert blueprint is not None, engine.trace.stop_reason
    assert len(blueprint.knowledge_units) == len(windows)
    assert "末尾不可截断" in engine.trace.calls[len(windows) - 1].prompt
    assert all(call.estimated_input_tokens <= call.input_budget_tokens for call in engine.trace.calls)
    assert all(call.input_budget_tokens + call.output_budget_tokens + 512 <= 32000 for call in engine.trace.calls)
    assert all(request.system_prompt and '"properties"' in request.system_prompt for request in engine.provider.calls)
    assert all(w.context_slices for w in windows)


def test_legacy_source_context_no_longer_discards_characters_after_500():
    from annotation.workflow.graph import _source_context
    _, blocks = source(["x" * 800 + "important theorem condition"])
    assert "important theorem condition" in _source_context(blocks)


def test_actual_chapter_tree_and_multiple_reduction_levels_preserve_every_unit():
    texts = []
    for chapter in range(1, 4):
        texts.append(f"第{chapter}章 主题{chapter}")
        texts.extend(f"{chapter}.{section_number} 内容{section_number}\n定义说明。" for section_number in range(1, 7))
    engine, windows, _ = engine_for(texts)
    blueprint = engine.run(windows)
    assert blueprint is not None, engine.trace.stop_reason
    assert len(blueprint.knowledge_units) == len(windows) == 21
    assert len(engine.trace.groups) >= 7
    assert all(len(g["children"]) <= 4 for g in engine.trace.groups)
    assert {n.node_id for n in engine.structure.nodes if n.owned_ranges} == {u.structure_node_ids[0] for u in blueprint.knowledge_units}
    assert all(w.status == "accepted" for w in engine.trace.windows)


def test_local_retry_does_not_regenerate_other_windows_and_new_run_reuses_cache():
    attempts = {}

    def decide(request):
        if request.schema == LocalBlueprintDraft:
            window = section(request.prompt, "Location and source window")
            key = window["window_id"]
            attempts[key] = attempts.get(key, 0) + 1
            if "第二" in window["path"][-1] and attempts[key] == 1:
                return {"title": "bad", "knowledge_units": []}

    provider = TeachingProvider(decide)
    texts = ["第1章 第一部分", "第2章 第二部分"]
    engine, windows, _ = engine_for(texts, provider)
    assert engine.run(windows) is not None, engine.trace.stop_reason
    assert sorted(attempts.values()) == [1, 2]
    assert any(call.route == "revise" for call in engine.trace.calls)
    again, windows, _ = engine_for(texts, TeachingProvider(), run_id="new-run")
    blueprint = again.run(windows)
    assert blueprint is not None, again.trace.stop_reason
    assert all(u.run_id == "new-run" for u in blueprint.knowledge_units)
    assert all(c.cache_hit for c in again.trace.calls)
    assert again.provider.calls == []


@pytest.mark.parametrize("change", ["content", "model", "prompt", "corrupt"])
def test_cache_invalidates_on_changed_inputs_or_corruption(change, monkeypatch):
    texts = ["第1章 第一部分", "第2章 第二部分"]
    engine, windows, _ = engine_for(texts)
    assert engine.run(windows)
    if change == "content":
        texts[0] += "\n新知识"
    next_provider = TeachingProvider()
    if change == "model":
        next_provider.model = "another-model"
    if change == "prompt":
        import annotation.workflow.blueprint_hierarchy as module
        original = module.load_prompt
        monkeypatch.setattr(module, "load_prompt", lambda *a, **kw: original(*a, **kw) + "\nUpdated prompt")
        # The test provider's parser expects JSON terminated by a heading.
        next_provider.decide = lambda request: None
    if change == "corrupt":
        for path in engine.cache_dir.glob("*.json"):
            path.write_text("broken cache", encoding="utf-8")
    next_engine, windows, _ = engine_for(texts, next_provider)
    assert next_engine.run(windows), next_engine.trace.stop_reason
    assert next_provider.calls


def test_llm_can_skip_non_teaching_text_with_reason_but_cannot_silently_skip():
    def decide(request):
        if request.schema == LocalBlueprintDraft:
            window = section(request.prompt, "Location and source window")
            if window["owned"][0]["text"] == "目录":
                return {"title": "目录", "knowledge_units": [], "skip_reason": "这里只列出目录，没有独立教学内容"}
    engine, windows, _ = engine_for(["目录", "第1章 正文"], TeachingProvider(decide))
    assert engine.run(windows), engine.trace.stop_reason
    assert [w.status for w in engine.trace.windows] == ["skipped", "accepted"]
    bad, windows, _ = engine_for(["第1章 不得跳过", "第2章 正文"],
        TeachingProvider(lambda request: {"title": "bad", "knowledge_units": []} if request.schema == LocalBlueprintDraft else None))
    assert bad.run(windows) is None
    assert bad.trace.windows[0].status == "blocked"
    assert bad.trace.windows[1].status == "pending"
    assert len(bad.provider.calls) == 3


def test_coordinator_tools_resolve_cross_chapter_prerequisite_and_read_source():
    def decide(request):
        if request.schema == LocalBlueprintDraft:
            window = section(request.prompt, "Location and source window")
            advanced = "进阶" in window["path"][-1]
            return {"title": window["path"][-1], "summary": window["path"][-1], "representative_unit_ids": ["unit"],
                "knowledge_units": [{"knowledge_unit_id": "unit", "title": "进阶" if advanced else "基础", "kind": "concept",
                    "learning_objectives": ["理解"], "prerequisites": ["基础"] if advanced else [],
                    "source_refs": [window["owned"][0]["source_ref"]]}]}
        groups = section(request.prompt, "Groups")
        step = request.metadata["scope_id"].split(":step-")[-1]
        if step == "1":
            return {"action": "read", "requests": [{"tool": "search_units", "query": "基础"}]}
        if step == "2":
            return {"action": "read", "requests": [{"tool": "read_sources", "ids": ["src-0"]}]}
        ids = {r["title"]: r["unit_id"] for g in groups for r in g["representatives"]}
        return {"action": "finalize", "group_order": list(reversed([g["group_id"] for g in groups])),
            "unit_updates": [{"unit_id": ids["进阶"], "prerequisites": [ids["基础"]]}], "summary": "先基础，再进阶"}
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶"], TeachingProvider(decide))
    blueprint = engine.run(windows)
    assert blueprint, engine.trace.stop_reason
    assert [u.title for u in blueprint.knowledge_units] == ["基础", "进阶"]
    assert blueprint.knowledge_units[1].prerequisites == [blueprint.knowledge_units[0].artifact_id]
    assert [t["request"]["tool"] for t in engine.trace.tool_calls] == ["search_units", "read_sources"]


def test_explicit_merge_preserves_provenance_and_learning_objectives():
    def decide(request):
        if request.schema != CoordinationDecision:
            return None
        groups = section(request.prompt, "Groups")
        ids = [g["representatives"][0]["unit_id"] for g in groups]
        return {"action": "finalize", "group_order": [g["group_id"] for g in groups],
            "merges": [{"duplicate_id": ids[1], "canonical_id": ids[0], "reason": "两节介绍同一基础定义"}],
            "representative_unit_ids": [ids[0]], "summary": "合并重复定义"}
    engine, windows, _ = engine_for(["第1章 基础", "第2章 重复基础"], TeachingProvider(decide))
    blueprint = engine.run(windows)
    assert blueprint, engine.trace.stop_reason
    assert len(blueprint.knowledge_units) == 1
    unit = blueprint.knowledge_units[0]
    assert set(unit.source_refs) == {"src-0", "src-1"}
    assert len(unit.structure_node_ids) == 2
    assert len(engine.trace.merges) == 1


@pytest.mark.parametrize("failure", ["drop_group", "cycle", "unknown_reference", "tools_exhausted", "authentication", "call_budget"])
def test_invalid_plans_and_exhaustion_stop_explicitly(failure, monkeypatch):
    if failure == "call_budget":
        monkeypatch.setenv("BLUEPRINT_MAX_CALLS", "1")
    def decide(request):
        if failure == "authentication":
            return ProviderError("bad credentials", category="authentication")
        if request.schema != CoordinationDecision:
            return None
        groups = section(request.prompt, "Groups")
        ids = [g["representatives"][0]["unit_id"] for g in groups]
        if failure == "tools_exhausted":
            return {"action": "read", "requests": [{"tool": "read_units", "ids": ids}]}
        result = {"action": "finalize", "group_order": [g["group_id"] for g in groups]}
        if failure == "drop_group":
            result["group_order"] = result["group_order"][:-1]
        if failure == "cycle":
            result["unit_updates"] = [{"unit_id": ids[0], "prerequisites": [ids[1]]}, {"unit_id": ids[1], "prerequisites": [ids[0]]}]
        if failure == "unknown_reference":
            result["unit_updates"] = [{"unit_id": ids[0], "prerequisites": ["unknown"]}]
        return result
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶"], TeachingProvider(decide))
    assert engine.run(windows) is None
    assert engine.trace.final_status == ("failed" if failure == "authentication" else "blocked")
    assert engine.trace.stop_reason != "not_started"
    if failure == "authentication":
        assert len(engine.provider.calls) == 1


def test_too_small_budget_fails_before_model_and_empty_source_is_blocked():
    document, blocks = source(["正文"])
    tree = build_textbook_structure(document, blocks)
    with pytest.raises(ProviderError, match="budget"):
        build_blueprint_windows(tree, blocks, PromptBudget(100, 100))
    empty, windows, _ = engine_for([])
    assert empty.run(windows) is None
    assert empty.provider.calls == []
    assert empty.trace.stop_reason == "no_extracted_text"


def test_hierarchical_workflow_persists_failed_and_successful_scan_diagnostics(monkeypatch):
    import annotation.workflow.graph as graph_module
    from annotation.workflow import run_minimal_workflow
    from annotation.workflow.persistence import _blueprint_loop_summary
    monkeypatch.setattr(graph_module, "parse_pdf", lambda _path, run_id: source(["第1章 基础", "第2章 进阶"], run_id=run_id))
    state = run_minimal_workflow(provider=TeachingProvider(), run_id="hierarchy-integration")
    assert state["blueprint_mode"] == "hierarchical"
    assert state["blueprint_hierarchy_trace"].final_status == "accepted"
    payload = json.loads(Path(state["blueprint_artifact_path"]).read_text(encoding="utf-8"))
    assert payload["schema_version"] == "blueprint-artifact-v3"
    assert len(payload["textbook_structure"]["nodes"]) == 3
    assert len(payload["hierarchy_trace"]["windows"]) == 2
    summary = _blueprint_loop_summary(state)
    assert summary["window_count"] == 2
    assert summary["accepted_window_count"] == 2
    assert summary["trace_path"] == state["blueprint_artifact_path"]
    assert "calls" not in summary
    # The intentionally empty architect response blocks downstream publishing.
    assert state["workflow_status"] == "blocked"
    assert state.get("document") is None


def test_hierarchical_workflow_reaches_document_with_offline_content_and_keeps_api_summary(monkeypatch, tmp_path):
    import annotation.workflow.graph as graph_module
    import annotation.api.routes as routes
    from annotation.workflow import run_minimal_workflow
    from annotation.api.persistence_service import persist_workflow_result, load_run_payload
    from annotation.persistence import PersistenceRepository
    from fastapi.testclient import TestClient
    from annotation.main import app

    class OfflineContentProvider(TeachingProvider):
        # Only downstream deterministic content fixtures use the mock seam;
        # local Blueprint and coordination still execute the real interfaces.
        provider = "mock"

    monkeypatch.setattr(graph_module, "parse_pdf", lambda _path, run_id: source(["第1章 基础", "第2章 进阶"], run_id=run_id))
    state = run_minimal_workflow(provider=OfflineContentProvider(), run_id="hierarchy-document")
    assert state["blueprint_mode"] == "hierarchical"
    assert state["blueprint_hierarchy_trace"].final_status == "accepted"
    assert state["workflow_status"] == "accepted"
    assert len(state["content_tasks"]) == len(state["blueprint"].knowledge_units) == 2
    assert state.get("document") is not None
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"offline fixture pdf")
    database = tmp_path / "annotation.sqlite3"
    repo = PersistenceRepository(database)
    book = repo.register_book(pdf)
    repo.create_run(book["book_id"], run_id=state["run_id"])
    persist_workflow_result(repo, book=book, state=state)
    loaded = load_run_payload(repo, state["run_id"])
    assert loaded["blueprint_loop"]["mode"] == "hierarchical"
    assert loaded["blueprint_loop"]["window_count"] == 2
    repo.close()
    monkeypatch.setattr(routes, "repository", lambda: PersistenceRepository(database))
    response = TestClient(app).get(f"/api/runs/{state['run_id']}")
    assert response.status_code == 200
    assert response.json()["blueprint_loop"]["mode"] == "hierarchical"
    assert "calls" not in response.json()["blueprint_loop"]


def test_failed_hierarchical_workflow_is_persisted_without_a_blueprint(monkeypatch):
    import annotation.workflow.graph as graph_module
    from annotation.workflow import run_minimal_workflow
    monkeypatch.setattr(graph_module, "parse_pdf", lambda _path, run_id: source(["第1章 基础", "第2章 进阶"], run_id=run_id))
    provider = TeachingProvider(lambda _request: ProviderError("authentication failed", category="authentication"))
    state = run_minimal_workflow(provider=provider, run_id="hierarchy-failed")
    assert state["workflow_status"] == "failed"
    payload = json.loads(Path(state["blueprint_artifact_path"]).read_text(encoding="utf-8"))
    assert payload["blueprint"] is None
    assert payload["hierarchy_trace"]["final_status"] == "failed"
    assert len(payload["hierarchy_trace"]["calls"]) == 1
    assert state.get("document") is None
