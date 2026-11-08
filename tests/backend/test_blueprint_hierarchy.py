from __future__ import annotations

import hashlib
import json
from pathlib import Path
from collections import Counter
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


def test_local_temporary_keys_map_relations_and_representatives_to_program_ids():
    def decide(request):
        if request.schema == LocalBlueprintDraft:
            return {"title": "Local", "representative_unit_ids": ["u2"], "knowledge_units": [
                {"knowledge_unit_id": "u1", "title": "First", "learning_objectives": ["First goal"],
                 "related_unit_ids": ["u2"]},
                {"knowledge_unit_id": "u2", "title": "Second", "learning_objectives": ["Second goal"],
                 "prerequisites": ["u1"]},
            ]}

    engine, windows, _ = engine_for(["第1章 正文"], TeachingProvider(decide))
    groups = []
    original_local = engine.local

    def capture_local(window):
        group = original_local(window)
        groups.append(group)
        return group

    engine.local = capture_local
    bp = engine.run(windows)
    assert bp is not None, engine.trace.stop_reason
    first, second = bp.knowledge_units
    assert first.artifact_id not in {"u1", "u2"}
    assert second.artifact_id not in {"u1", "u2", first.artifact_id}
    assert first.related_unit_ids == [second.artifact_id]
    assert second.prerequisites == [first.artifact_id]
    assert groups[0].representative_unit_ids == [second.artifact_id]


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


def test_actual_chapter_tree_and_multiple_reduction_levels_preserve_every_unit():
    texts = []
    for chapter in range(1, 4):
        texts.append(f"第{chapter}章 主题{chapter}")
        texts.extend(f"{chapter}.{section_number} 内容{section_number}\n定义说明。" for section_number in range(1, 7))
    engine, windows, _ = engine_for(texts)
    blueprint = engine.run(windows)
    assert blueprint is not None, engine.trace.stop_reason
    assert len(blueprint.knowledge_units) == len(windows) == 18
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
    coordination_calls = [call for call in engine.provider.calls if call.schema == CoordinationDecision]
    assert coordination_calls and all("### Evidence and dependency rules" in call.prompt for call in coordination_calls)
    assert coordination_calls[0].metadata["prompt_version"] == "coordinate_blueprint:v7:budgeted-evidence"


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
    duplicate_id = next(iter(engine.trace.merges))
    assert unit.objective_ids == [f"{unit.artifact_id}-o1", f"{duplicate_id}-o1"]
    assert len(unit.objective_texts) == 2
    assert unit.objective_texts[0] == unit.objective_texts[1]


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


@pytest.mark.parametrize("defect", ["omit", "duplicate", "truncate", "wrong_owner"])
def test_source_coverage_defects_stop_before_any_model_call(defect):
    engine, windows, _ = engine_for(["第1章 实数", "第2章 复数"])
    if defect == "omit":
        windows = windows[:1]
    elif defect == "duplicate":
        windows.append(windows[0])
    elif defect == "truncate":
        windows[-1].slices[-1].end -= 1
    else:
        windows[-1].node_id = windows[0].node_id
    assert engine.run(windows) is None
    assert engine.trace.final_status == "blocked"
    assert engine.provider.calls == []


def test_hierarchical_workflow_persists_failed_and_successful_scan_diagnostics(monkeypatch):
    import annotation.workflow.blueprint_nodes as graph_module
    from annotation.workflow import run_minimal_workflow
    from annotation.workflow.persistence import _blueprint_loop_summary
    monkeypatch.setattr(graph_module, "parse_pdf", lambda _path, run_id: source(["第1章 基础", "第2章 进阶"], run_id=run_id))
    state = run_minimal_workflow(provider=TeachingProvider(), run_id="hierarchy-integration")
    assert "blueprint_mode" not in state
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
    import annotation.workflow.blueprint_nodes as graph_module
    import annotation.api.routes as routes
    from annotation.workflow import run_minimal_workflow
    from annotation.api.persistence_service import persist_workflow_result, load_run_payload
    from annotation.persistence import PersistenceRepository
    from fastapi.testclient import TestClient
    from annotation.main import app

    class OfflineContentProvider(TeachingProvider):
        from annotation.offline.provider import OfflineWorkflowCapabilities

        provider = "offline"
        workflow = OfflineWorkflowCapabilities()

        def generate_structured(self, request):
            if request.schema not in {LocalBlueprintDraft, CoordinationDecision}:
                from annotation.offline import OfflineFixtureProvider
                return OfflineFixtureProvider().generate_structured(request)
            return super().generate_structured(request)

    monkeypatch.setattr(graph_module, "parse_pdf", lambda _path, run_id: source(["第1章 基础", "第2章 进阶"], run_id=run_id))
    state = run_minimal_workflow(provider=OfflineContentProvider(), run_id="hierarchy-document")
    assert "blueprint_mode" not in state
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
    import annotation.workflow.blueprint_nodes as graph_module
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


# Regression cases for the live chapter's failed coordination loop.
class ReadUntilFinal:
    def __call__(self, request):
        if request.schema == LocalBlueprintDraft:
            return None
        groups = section(request.prompt, "Groups")
        if request.schema.__name__ != "FinalCoordinationDecision":
            return {"action": "read", "requests": [{"tool": "read_units", "ids": [groups[0]["representatives"][0]["unit_id"]]}]}
        return {"action": "finalize", "group_order": [g["group_id"] for g in groups]}


class InheritedRelations:
    def __call__(self, request):
        if request.schema != LocalBlueprintDraft:
            return None
        window = section(request.prompt, "Location and source window")
        advanced = "进阶" in window["path"][-1]
        return {"title": "教学", "knowledge_units": [{"knowledge_unit_id": "u1",
            "title": "进阶" if advanced else "基础", "learning_objectives": ["理解"],
            "prerequisites": [" 基 础 "] if advanced else []}]}


def prepared_groups(engine, windows):
    from annotation.domain.textbook import WindowResult
    engine.trace.windows = [WindowResult(window=w) for w in windows]
    return [engine.local(w) for w in windows]


def test_last_decision_must_finalize_and_reads_are_not_cached(monkeypatch):
    monkeypatch.setenv("BLUEPRINT_COORDINATION_MAX_STEPS", "3")
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶"], TeachingProvider(ReadUntilFinal()))
    assert engine.run(windows), engine.trace.stop_reason
    assert engine.trace.tool_calls[1]["result"]["error"].startswith("already_read")
    last = engine.provider.calls[-1]
    assert last.schema.model_json_schema()["properties"]["action"]["const"] == "finalize"
    assert "Remaining decisions: 1" in last.prompt
    again, windows, _ = engine_for(["第1章 基础", "第2章 进阶"], TeachingProvider(ReadUntilFinal()))
    assert again.run(windows), again.trace.stop_reason
    assert len(again.provider.calls) == 2  # only the two read decisions repeat
    assert all(call.schema == CoordinationDecision for call in again.provider.calls)


def test_inherited_title_relation_resolves_without_coordinator_update():
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶"], TeachingProvider(InheritedRelations()))
    blueprint = engine.run(windows)
    assert blueprint, engine.trace.stop_reason
    assert blueprint.knowledge_units[1].prerequisites == [blueprint.knowledge_units[0].artifact_id]
    assert engine.trace.ordering["moved_count"] == 0


def test_chinese_search_rephrasing_path_ties_and_pagination():
    from annotation.workflow.blueprint_hierarchy import BlueprintToolRequest
    engine, windows, _ = engine_for(["第1章 上确界与下确界", "第2章 数学归纳法", "第3章 复数"])
    prepared_groups(engine, windows)
    first = engine.tool(BlueprintToolRequest(tool="search_units", query="上确界与下确界的定义"))
    assert first["items"][0]["title"] == "第1章 上确界与下确界"
    second = engine.tool(BlueprintToolRequest(tool="search_units", query="数学归纳法原理"))
    assert second["items"][0]["title"] == "第2章 数学归纳法"
    assert all(0.3 <= item["score"] <= 1 for item in second["items"])
    assert second["items"][0]["path"]
    assert not engine.tool(BlueprintToolRequest(tool="search_units", query=""))["items"]
    assert not engine.tool(BlueprintToolRequest(tool="search_units", query="quasar"))["items"]
    assert not engine.tool(BlueprintToolRequest(tool="search_units", query="数学", offset=100))["items"]


def test_ambiguous_titles_block_both_coordination_and_final_gate():
    from annotation.workflow.blueprint_checks import validate_blueprint
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶", "第3章 小结"])
    groups = prepared_groups(engine, windows)
    units = list(engine.units.values())
    units[0].title = "小结"
    units[1].title = " 小 结 "
    units[2].prerequisites = ["小结"]
    decision = CoordinationDecision(action="finalize", group_order=[g.group_id for g in groups])
    original = [u.model_dump() for u in units]
    with pytest.raises(ValueError, match="未解析"):
        engine._check_coordination(decision, groups, set(engine.units))
    assert [u.model_dump() for u in units] == original
    assert validate_blueprint(engine._blueprint(units), valid_source_refs=set()).status == "needs_revision"


@pytest.mark.parametrize("change", ["thinking", "steps", "budget"])
def test_cache_key_covers_behavioral_parameters(change):
    engine, _, _ = engine_for(["第1章 基础"])
    old = engine._cache_key("coordinate_blueprint", "prompt", CoordinationDecision)
    if change == "thinking":
        engine.provider.thinking = "enabled"
    if change == "steps":
        engine.tool_steps += 1
    if change == "budget":
        engine.budget = PromptBudget(engine.budget.input_tokens - 1, engine.budget.output_tokens)
    assert engine._cache_key("coordinate_blueprint", "prompt", CoordinationDecision) != old


@pytest.mark.parametrize("invalid", ["self_merge", "foreign_representative", "self_prerequisite"])
def test_invalid_decision_is_rejected_without_silent_filtering(invalid):
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶"])
    groups = prepared_groups(engine, windows)
    unit_id = next(iter(engine.units))
    data = {"action": "finalize", "group_order": [g.group_id for g in groups]}
    if invalid == "self_merge":
        data["merges"] = [{"duplicate_id": unit_id, "canonical_id": unit_id, "reason": "same"}]
    if invalid == "foreign_representative":
        data["representative_unit_ids"] = ["unknown"]
    if invalid == "self_prerequisite":
        data["unit_updates"] = [{"unit_id": unit_id, "prerequisites": [engine.units[unit_id].title]}]
    decision = CoordinationDecision.model_validate(data)
    before = decision.model_dump()
    with pytest.raises(ValueError):
        engine._check_coordination(decision, groups, set(engine.units))
    assert decision.model_dump() == before


def test_unknown_tool_ids_return_errors_and_topology_is_stable():
    from annotation.workflow.blueprint_hierarchy import BlueprintToolRequest, _dependency_order
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶", "第3章 无关"])
    prepared_groups(engine, windows)
    assert engine.tool(BlueprintToolRequest(tool="read_units", ids=["unknown"]))["items"][0]["error"] == "unknown_unit"
    assert engine.tool(BlueprintToolRequest(tool="read_sources", ids=["unknown"]))["items"][0]["error"] == "unknown_source"
    ids = list(engine.units)
    order = [ids[2], ids[1], ids[0]]
    engine.units[ids[1]].prerequisites = [ids[0]]
    assert _dependency_order(order, engine.units) == [ids[2], ids[0], ids[1]]
    engine.units[ids[0]].prerequisites = [ids[1]]
    with pytest.raises(ValueError, match="cyclic"):
        _dependency_order(order, engine.units)


def test_search_ties_have_stable_id_order_and_offsets():
    from annotation.workflow.blueprint_hierarchy import BlueprintToolRequest
    engine, windows, _ = engine_for([f"第{i}章 基础" for i in range(1, 8)])
    prepared_groups(engine, windows)
    first = engine.tool(BlueprintToolRequest(tool="search_units", query="基础"))
    second = engine.tool(BlueprintToolRequest(tool="search_units", query="基础", offset=5))
    ids = [item["unit_id"] for item in first["items"] + second["items"]]
    assert ids == sorted(engine.units)
    assert first["next_offset"] == 5
    assert second["next_offset"] is None


def test_normalized_references_are_audited_and_objectives_are_not_aliases():
    from annotation.workflow.blueprint_references import normalize_relationships, resolve_reference, title_index
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶"])
    prepared_groups(engine, windows)
    first, second = list(engine.units.values())
    second.prerequisites = [first.title.replace("基础", "基 础")]
    changes = normalize_relationships(engine.units)
    assert changes == [{"unit_id": second.artifact_id, "field": "prerequisites",
                        "original": "第1章 基 础", "resolved": first.artifact_id}]
    assert normalize_relationships(engine.units) == []
    assert resolve_reference(first.objective_texts[0], set(engine.units), title_index(engine.units.values())) is None


def test_paragraph_boundaries_are_preferred_and_ownership_is_lossless():
    paragraph = "定义与条件必须一起理解。" * 450 + "\n\n"
    text = paragraph * 5
    engine, windows, blocks = engine_for([text])
    assert len(windows) > 1
    spans = [span for window in windows for span in window.slices]
    assert "".join(text[span.start:span.end] for span in spans) == text
    assert all(span.end % len(paragraph) == 0 for span in spans)
    assert engine.run(windows) is not None, engine.trace.stop_reason


def test_continuation_flags_offer_adjacent_candidates_without_automatic_merging():
    def decide(request):
        if request.schema == LocalBlueprintDraft:
            window = request.context["window"]
            return {"title": window.path[-1], "knowledge_units": [{"knowledge_unit_id": "u",
                "title": window.path[-1], "learning_objectives": ["理解片段"]}],
                "continues_after": ["u"] if "第一" in window.path[-1] else [],
                "continues_before": ["u"] if "第二" in window.path[-1] else []}
    engine, windows, _ = engine_for(["第1章 第一部分", "第2章 第二部分"], TeachingProvider(decide))
    bp = engine.run(windows)
    assert bp is not None, engine.trace.stop_reason
    assert len(bp.knowledge_units) == 2
    cards = section(next(call.prompt for call in engine.trace.calls if call.phase == "coordinate_blueprint"), "Groups")
    pair = cards[0]["boundary_candidates"][0]
    assert pair["left_id"] == bp.knowledge_units[0].artifact_id
    assert pair["right_id"] == bp.knowledge_units[1].artifact_id
    assert pair["continues_after"] and pair["continues_before"]
    assert not engine.trace.merges


def test_ambiguous_reference_has_candidates_and_unresolved_audit_on_failure():
    def decide(request):
        if request.schema == LocalBlueprintDraft:
            return {"title": "Local", "knowledge_units": [
                {"knowledge_unit_id": "a", "title": "导数", "learning_objectives": ["导数定义"]},
                {"knowledge_unit_id": "b", "title": "导数", "learning_objectives": ["导数计算"]},
                {"knowledge_unit_id": "c", "title": "应用", "learning_objectives": ["应用"], "prerequisites": ["导数"]}]}
    engine, windows, _ = engine_for(["正文"], TeachingProvider(decide), attempts=1)
    assert engine.run(windows) is None
    unresolved = engine.trace.unresolved_references[0]
    assert unresolved["reference"] == "导数"
    assert len(unresolved["candidates"]) == 2
    assert len({c["unit_id"] for c in unresolved["candidates"]}) == 2
    assert all("path" in c and "score" in c for c in unresolved["candidates"])


def test_final_gate_repairs_only_affected_units_without_rescanning():
    from annotation.workflow.blueprint_hierarchy import PlanGroup
    def decide(request):
        if request.metadata["agent"] == "resolve_blueprint_relations":
            return {"resolutions": [{"reference_id": request.context["relations"][0]["reference_id"],
                "outcome": "remove", "reason": "错误加入的内部引用"}]}
    engine, windows, _ = engine_for(["第1章 第一部分", "第2章 第二部分"], TeachingProvider(decide))
    bp = engine.run(windows)
    assert bp is not None, engine.trace.stop_reason
    before = len(engine.provider.calls)
    engine.units[bp.knowledge_units[0].artifact_id].prerequisites = ["unknown prerequisite"]
    ids = [u.artifact_id for u in bp.knowledge_units]
    order, issues = engine._finish(PlanGroup(group_id="all", title="Book", summary="Book",
        unit_ids=ids, representative_unit_ids=ids))
    assert order == ids
    assert not any(i.severity == "blocking" for i in issues)
    assert len(engine.provider.calls) == before + 2
    assert engine.provider.calls[-2].metadata["agent"] == "resolve_blueprint_relations"
    assert engine.trace.reference_resolutions[-1]["reason"] == "错误加入的内部引用"
    assert engine.provider.calls[-1].metadata["agent"] == "coordinate_blueprint"
    repaired = next(group for group in engine.trace.groups if group["scope_id"] == "group-final-repair")
    assert len(repaired["result"]["unit_ids"]) == 1


def test_external_prerequisites_survive_local_generation_and_do_not_become_graph_edges():
    from annotation.workflow.blueprint_checks import validate_blueprint
    from annotation.workflow.blueprint_hierarchy import _dependency_order
    def decide(request):
        if request.schema == LocalBlueprintDraft:
            return {"title": "应用", "knowledge_units": [{"knowledge_unit_id": "u",
                "title": "函数极限", "learning_objectives": ["理解极限"],
                "external_prerequisites": ["集合的基本记号"]}]}
    engine, windows, _ = engine_for(["教材正文"], TeachingProvider(decide))
    bp = engine.run(windows)
    assert bp is not None, engine.trace.stop_reason
    unit = bp.knowledge_units[0]
    assert unit.external_prerequisites == ["集合的基本记号"]
    assert unit.prerequisites == []
    assert validate_blueprint(bp, valid_source_refs=set(engine.blocks)).status == "accepted"
    assert _dependency_order([unit.artifact_id], engine.units) == [unit.artifact_id]
    unit.prerequisites = ["不存在的内部引用"]
    assert validate_blueprint(bp, valid_source_refs=set(engine.blocks)).status == "needs_revision"


def test_external_prerequisites_survive_explicit_merge():
    def decide(request):
        if request.schema == LocalBlueprintDraft:
            window = request.context["window"]
            return {"title": "重复定义", "knowledge_units": [{"knowledge_unit_id": "u",
                "title": window.path[-1], "learning_objectives": ["理解定义"],
                "external_prerequisites": [window.path[-1] + "的外部基础"]}]}
        groups = request.context["groups"]
        ids = [g["representatives"][0]["unit_id"] for g in groups]
        return {"action": "finalize", "group_order": [g["group_id"] for g in groups],
            "merges": [{"duplicate_id": ids[1], "canonical_id": ids[0], "reason": "重复内容"}]}
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶"], TeachingProvider(decide))
    bp = engine.run(windows)
    assert bp is not None, engine.trace.stop_reason
    assert bp.knowledge_units[0].external_prerequisites == ["第1章 基础的外部基础", "第2章 进阶的外部基础"]


def test_many_unresolved_relations_are_applied_in_bounded_batches_with_audit():
    from annotation.workflow.blueprint_relations import repair_relationships
    from annotation.workflow.blueprint_hierarchy import PlanGroup
    counts = []
    def decide(request):
        if request.metadata["agent"] == "resolve_blueprint_relations":
            batch = request.context["relations"]
            counts.append(len(batch))
            # Deliberately apply only part of a batch; later calls must retain the rest.
            return {"resolutions": [{"reference_id": r["reference_id"], "outcome": "external",
                "reason": "本章假定读者已掌握的外部基础"} for r in batch[:2]]}
    engine, windows, _ = engine_for(["教材正文"], TeachingProvider(decide))
    groups = prepared_groups(engine, windows)
    unit = next(iter(engine.units.values()))
    unit.prerequisites = [f"外部基础{i}" for i in range(35)]
    before = engine._card(groups[0])
    assert "unresolved_relations" not in before and before["unresolved_count"] == 35
    repair_relationships(engine, list(engine.units))
    unit = engine.units[unit.artifact_id]
    assert unit.prerequisites == []
    assert len(unit.external_prerequisites) == 35
    assert len(counts) > 1 and 8 < max(counts) <= 32
    assert len(engine.trace.reference_resolutions) == 35
    assert all(r["outcome"] == "external" and r["reason"] for r in engine.trace.reference_resolutions)
    assert all(c.estimated_input_tokens <= c.input_budget_tokens for c in engine.trace.calls)


def test_invalid_relation_batch_is_atomic_and_ambiguous_titles_are_not_external():
    from annotation.workflow.blueprint_relations import repair_relationships
    from annotation.workflow.blueprint_hierarchy import HierarchyStop
    def decide(request):
        if request.metadata["agent"] == "resolve_blueprint_relations":
            return {"resolutions": [{"reference_id": r["reference_id"], "outcome": "external",
                "reason": "不能将歧义内部引用作为外部前置"} for r in request.context["relations"]]}
    engine, windows, _ = engine_for(["第1章 基础", "第2章 基础", "第3章 应用"], TeachingProvider(decide), attempts=1)
    prepared_groups(engine, windows)
    a, b, owner = list(engine.units.values())
    a.title = b.title = "导数"
    owner.prerequisites = ["外部概念", "导数"]
    before = {k: u.model_dump() for k, u in engine.units.items()}
    with pytest.raises(HierarchyStop, match="ambiguous internal"):
        repair_relationships(engine, [owner.artifact_id])
    assert {k: u.model_dump() for k, u in engine.units.items()} == before
    assert engine.trace.reference_resolutions == []


def test_read_units_projects_fields_and_pages_large_objectives_losslessly():
    from annotation.workflow.blueprint_hierarchy import BlueprintToolRequest
    from annotation.workflow.budget import encoded_size
    engine, windows, _ = engine_for(["教材正文"])
    prepared_groups(engine, windows)
    unit = next(iter(engine.units.values()))
    short = engine.tool(BlueprintToolRequest(tool="read_units", ids=[unit.artifact_id]))
    assert "run_id" not in short["items"][0]
    assert "objectives" in short["items"][0]
    long_text = "必须保留的完整学习目标。" * 1000
    unit.learning_objectives[0] = unit.learning_objectives[0].model_copy(update={"text": long_text})
    offset = 0
    pieces = []
    while True:
        page = engine.tool(BlueprintToolRequest(tool="read_units", ids=[unit.artifact_id], offset=offset))
        assert encoded_size(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= min(4096, engine.budget.input_tokens // 4)
        assert page["items"]
        pieces.extend(item for item in page["items"] if item.get("field") == "objectives")
        if page["next_offset"] is None:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert "".join(p["text"] for p in sorted(pieces, key=lambda p: p["text_start"])) == long_text


def test_single_group_over_budget_has_scope_and_never_calls_empty_batch(monkeypatch):
    from annotation.workflow.blueprint_hierarchy import HierarchyStop
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶"])
    groups = prepared_groups(engine, windows)
    monkeypatch.setattr(engine, "_coordination_fits", lambda groups: False)
    before = len(engine.provider.calls)
    with pytest.raises(HierarchyStop, match=f"coordination_single_group_over_budget:{groups[0].group_id}:node=Root"):
        engine._reduce_groups(groups, "Root")
    assert len(engine.provider.calls) == before


def test_group_cards_stay_bounded_when_relations_and_titles_grow():
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶"])
    groups = prepared_groups(engine, windows)
    for unit in engine.units.values():
        unit.title = "很长的单元标题" * 1000
        unit.prerequisites = [f"未解析概念{i}" for i in range(1000)]
    card = engine._card(groups[0])
    assert card["unresolved_count"] == 1000
    assert len(json.dumps(card, ensure_ascii=False)) < 2000
    assert engine._coordination_fits(groups)


def test_relation_repair_can_read_evidence_and_then_apply_internal_target():
    from annotation.workflow.blueprint_relations import repair_relationships
    from annotation.workflow.blueprint_hierarchy import BlueprintToolRequest
    def decide(request):
        if request.metadata["agent"] == "resolve_blueprint_relations":
            if request.metadata["scope_id"].endswith(":step-1"):
                return {"action": "read", "requests": [{"tool": "read_units", "ids": [target]}]}
            return {"resolutions": [{"reference_id": request.context["relations"][0]["reference_id"],
                "outcome": "internal", "target_id": target, "reason": "结合学习目标确认同一概念"}]}
    engine, windows, _ = engine_for(["第1章 基础", "第2章 应用"], TeachingProvider(decide))
    prepared_groups(engine, windows)
    target, owner = list(engine.units)
    engine.units[owner].prerequisites = ["基础知识的另一种叫法"]
    repair_relationships(engine, [owner])
    assert engine.units[owner].prerequisites == [target]
    assert engine.trace.tool_calls[-1]["request"]["tool"] == "read_units"
    calls = [c for c in engine.trace.calls if c.phase == "resolve_blueprint_relations"]
    assert calls[0].parsed_output["action"] == "read"
    assert engine.units[target].title in calls[1].prompt
    assert all(c.estimated_input_tokens <= c.input_budget_tokens for c in calls)


def test_small_output_budget_splits_relation_repair_further():
    from annotation.workflow.blueprint_relations import repair_relationships
    def decide(request):
        if request.metadata["agent"] == "resolve_blueprint_relations":
            return {"resolutions": [{"reference_id": r["reference_id"], "outcome": "external",
                "reason": "必要的外部基础知识"} for r in request.context["relations"]]}
    engine, windows, _ = engine_for(["教材正文"], TeachingProvider(decide), budget=PromptBudget(16000, 1000))
    prepared_groups(engine, windows)
    owner = next(iter(engine.units))
    engine.units[owner].prerequisites = [f"背景知识{i}" for i in range(5)]
    repair_relationships(engine, [owner])
    calls = [c for c in engine.trace.calls if c.phase == "resolve_blueprint_relations"]
    assert len(calls) == 5
    assert len(engine.units[owner].external_prerequisites) == 5


def test_token_bounded_cards_and_tools_handle_expensive_unicode():
    from annotation.workflow.blueprint_hierarchy import BlueprintToolRequest
    from annotation.workflow.budget import encoded_size
    engine, windows, _ = engine_for(["第1章 基础", "第2章 进阶"])
    groups = prepared_groups(engine, windows)
    text = "🧬𠮷🪷" * 1000
    for unit in engine.units.values():
        unit.title = text
        unit.learning_objectives[0] = unit.learning_objectives[0].model_copy(update={"text": text})
    for group in groups:
        group.title = group.summary = text
    assert engine._coordination_fits(groups)
    owner = next(iter(engine.units))
    offset = 0
    observed = []
    while True:
        page = engine.tool(BlueprintToolRequest(tool="read_units", ids=[owner], offset=offset))
        assert page["items"] and encoded_size(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= min(4096, engine.budget.input_tokens // 4)
        observed.extend(p for p in page["items"] if p.get("field") == "objectives")
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert "".join(p["text"] for p in observed) == text


def test_coordinator_cannot_silently_remove_existing_relationships():
    engine, windows, _ = engine_for(["第1章 基础", "第2章 应用"])
    groups = prepared_groups(engine, windows)
    target, owner = list(engine.units)
    engine.units[owner].prerequisites = [target]
    decision = CoordinationDecision(action="finalize", group_order=[g.group_id for g in groups],
        unit_updates=[{"unit_id": owner, "prerequisites": []}])
    with pytest.raises(ValueError, match="requires an explicit reason"):
        engine._check_coordination(decision, groups, set(engine.units))


def test_unknown_internal_id_cannot_be_reclassified_as_external():
    from annotation.workflow.blueprint_relations import repair_relationships
    from annotation.workflow.blueprint_hierarchy import HierarchyStop
    def decide(request):
        if request.metadata["agent"] == "resolve_blueprint_relations":
            return {"resolutions": [{"reference_id": request.context["relations"][0]["reference_id"],
                "outcome": "external", "reason": "未知 ID 不能伪装成外部概念"}]}
    engine, windows, _ = engine_for(["教材正文"], TeachingProvider(decide), attempts=1)
    prepared_groups(engine, windows)
    owner = next(iter(engine.units))
    engine.units[owner].prerequisites = ["ku-hallucinated-id"]
    with pytest.raises(HierarchyStop, match="cannot be classified as external"):
        repair_relationships(engine, [owner])
    assert engine.units[owner].prerequisites == ["ku-hallucinated-id"]
    assert engine.units[owner].external_prerequisites == []


def test_page_bookmark_series_does_not_fragment_source_or_hide_real_bookmarks():
    texts = ['# 第1章 实数', '1.1 有理数', '定义', '页眉', '正文', '正文', '页眉', '1.2 完备性', '正文']
    toc = [[1, f'数学分析(原书第2版) 扫描版 {page + 13}', page] for page in range(1, 4)]
    toc.append([2, '1.2 完备性', 3])
    document, blocks = source(texts, toc=toc)
    tree = build_textbook_structure(document, blocks)
    assert sum(n.origin == 'bookmark' for n in tree.nodes) == 1
    assert next(n for n in tree.nodes if n.origin == 'bookmark').title == '1.2 完备性'
    assert sum(w.startswith('ignored_page_bookmark:') for w in tree.warnings) == 3
    owned = [i for n in tree.nodes for r in n.owned_ranges for i in range(r.start, r.end)]
    assert sorted(owned) == list(range(len(blocks)))
    assert next(n for n in tree.nodes if n.title == '# 第1章 实数'.lstrip('# ')).owned_ranges == []


def test_real_numbered_bookmarks_keep_chapter_hierarchy_and_introduction():
    document, blocks = source(['Chapter 1', 'Introduction prose', 'Text', 'Chapter 2', 'Text', 'Text', 'Chapter 3', 'Text'],
        toc=[[1, 'Chapter 1', 1], [1, 'Chapter 2', 2], [1, 'Chapter 3', 3]])
    tree = build_textbook_structure(document, blocks)
    assert sum(n.origin == 'bookmark' for n in tree.nodes) == 3
    assert not any(w.startswith('ignored_page_bookmark') for w in tree.warnings)
    assert tree.nodes[1].owned_ranges[0].start == 0


def test_multi_unit_tool_pages_supply_executable_continuation_and_cover_all_units():
    from annotation.workflow.blueprint_hierarchy import BlueprintToolRequest
    from annotation.workflow.budget import encoded_size
    engine, windows, _ = engine_for([f'第{i}章 内容{i}' for i in range(1, 9)])
    prepared_groups(engine, windows)
    ids = list(engine.units)
    request = BlueprintToolRequest(tool='read_units', ids=ids)
    observed = []
    pages = 0
    while True:
        page = engine.tool(request)
        pages += 1
        assert encoded_size(json.dumps(page, ensure_ascii=False, separators=(',', ':'))) <= 4000
        observed.extend(item['unit_id'] for item in page['items'])
        if page['next_offset'] is None:
            break
        request = BlueprintToolRequest.model_validate(page['continuation'])
        assert request.ids == ids
        assert request.offset == page['next_offset']
    assert observed == ids
    assert pages < 8


def test_regrouping_ids_cannot_duplicate_retained_evidence_in_prompt():
    from annotation.workflow.blueprint_hierarchy import BlueprintToolRequest
    engine, windows, _ = engine_for(['第1章 基础', '第2章 进阶', '第3章 综合'])
    prepared_groups(engine, windows)
    ids = list(engine.units)
    make_prompt = lambda evidence: json.dumps(evidence, ensure_ascii=False)
    history = engine._read_history([BlueprintToolRequest(tool='read_units', ids=ids[:2])], [], set(), make_prompt, CoordinationDecision, 'test', 0)
    history = engine._read_history([BlueprintToolRequest(tool='read_units', ids=ids[1:])], history, set(), make_prompt, CoordinationDecision, 'test', 1)
    items = [item for receipt in history for item in receipt.get('items', [])]
    assert Counter(item['unit_id'] for item in items) == Counter(ids)
    assert history[-1]['already_in_history'] == 1
    assert len(engine.trace.tool_calls[-1]['result']['items']) == 2


def test_cross_level_evidence_reuses_current_units_and_invalidates_changed_unit():
    from annotation.workflow.blueprint_hierarchy import BlueprintToolRequest
    engine, windows, _ = engine_for(['第1章 基础', '第2章 进阶'])
    prepared_groups(engine, windows)
    ids = list(engine.units)
    engine.tool(BlueprintToolRequest(tool='read_units', ids=ids))
    make_prompt = lambda evidence: json.dumps(evidence, ensure_ascii=False)
    history = engine._seed_evidence(set(ids), make_prompt, CoordinationDecision)
    assert {item['unit_id'] for receipt in history for item in receipt['items']} == set(ids)
    engine.units[ids[0]].external_prerequisites.append('新发现的外部先修')
    history = engine._seed_evidence(set(ids), make_prompt, CoordinationDecision)
    assert {item['unit_id'] for receipt in history for item in receipt['items']} == {ids[1]}


def test_small_input_budget_multiple_read_requests_fit_with_history_eviction():
    from annotation.workflow.blueprint_hierarchy import BlueprintToolRequest
    engine, windows, _ = engine_for(['第1章 基础', '第2章 进阶', '第3章 综合'], budget=PromptBudget(6000, 1000))
    prepared_groups(engine, windows)
    ids = list(engine.units)
    for unit in engine.units.values():
        unit.learning_objectives[0] = unit.learning_objectives[0].model_copy(update={'text': '必须保留的依据。' * 1000})
    make_prompt = lambda evidence: '规则' * 300 + json.dumps(evidence, ensure_ascii=False)
    history = [{'tool': 'old', 'items': [{'text': '历史依据。' * 1500}]}]
    requests = [BlueprintToolRequest(tool='read_units', ids=[uid]) for uid in ids]
    result = engine._read_history(requests, history, set(), make_prompt, CoordinationDecision, 'small', 0)
    assert PromptBudget(5232, 1000).fits(make_prompt(result), CoordinationDecision)
    assert any(r.get('omitted') for r in result)
    assert len(engine.trace.tool_calls) == 3
    assert all(c['result'].get('continuation') for c in engine.trace.tool_calls)


def test_relation_batch_can_commit_more_than_eight_without_weakening_validation():
    from annotation.workflow.blueprint_relations import repair_relationships
    sizes = []
    def decide(request):
        if request.metadata['agent'] == 'resolve_blueprint_relations':
            batch = request.context['relations']
            sizes.append(len(batch))
            return {'resolutions': [{'reference_id': r['reference_id'], 'outcome': 'external',
                'reason': '本教材明确假定的背景知识'} for r in batch]}
    engine, windows, _ = engine_for(['正文'], TeachingProvider(decide))
    prepared_groups(engine, windows)
    unit_id = next(iter(engine.units))
    engine.units[unit_id].prerequisites = [f'先修背景{i}' for i in range(12)]
    repair_relationships(engine, [unit_id])
    assert sizes == [12]
    assert not engine.units[unit_id].prerequisites
    assert len(engine.units[unit_id].external_prerequisites) == 12
    assert engine.trace.calls[-1].estimated_input_tokens <= engine.budget.input_tokens



def test_numbered_bookmark_series_matching_real_text_is_never_discarded():
    document, blocks = source(['Supplement notes 1', 'prose', 'prose',
        'Supplement notes 2', 'prose', 'prose', 'Supplement notes 3', 'prose'],
        toc=[[1, f'Supplement notes {page}', page] for page in range(1, 4)])
    tree = build_textbook_structure(document, blocks)
    assert [n.title for n in tree.nodes if n.origin == 'bookmark'] == [f'Supplement notes {page}' for page in range(1, 4)]
    assert not any(w.startswith('ignored_page_bookmark:') for w in tree.warnings)


def test_local_cache_survives_coordinator_policy_and_step_changes(monkeypatch):
    import annotation.workflow.blueprint_hierarchy as module
    engine, _, _ = engine_for(['正文'])
    key = engine._cache_key('generate_local_blueprint', 'prompt', LocalBlueprintDraft)
    engine.tool_steps += 1
    monkeypatch.setattr(module, 'COORDINATION_PROMPT_VERSION', 'new-coordination-policy')
    assert engine._cache_key('generate_local_blueprint', 'prompt', LocalBlueprintDraft) == key
