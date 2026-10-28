"""Mode routing, source isolation and model-selected modes across workflow stages."""
from dataclasses import replace

import pytest
from pydantic import ValidationError

from annotation.fact_checking.contracts import EvidenceKind, EvidenceRecord, SearchResult, SearchStatus
from annotation.retrieval.tool import HybridTextbookSearchTool, UnifiedRetrievalService
from annotation.retrieval.llamaindex_chroma import UnavailableVectorTextbookSearchTool
from annotation.workflow.models import RetrievalDecision, QuizRetrievalDecision, FactCheckRetrievalDecision
from test_content_context import _block


class Branch:
    version = "test"

    def __init__(self, name, refs=("chunk",), fail=False):
        self.name, self.refs, self.fail = name, refs, fail
        self.calls = []

    def search(self, query, *, limit=5):
        self.calls.append(query)
        if self.fail:
            raise TimeoutError("provider timeout")
        return SearchResult(skill_name=self.name, skill_version=self.version, query=query,
            status=SearchStatus.OK, evidence=tuple(EvidenceRecord(evidence_id=ref,
                kind=EvidenceKind.TEXTBOOK, query=query, text="bounded evidence", excerpt="bounded evidence",
                locator="page:1;block:0", provider=self.name, text_hash="hash", source_ref=ref)
                for ref in self.refs[:limit]))


@pytest.mark.parametrize("mode,counts", [("keyword", (1, 0)), ("vector", (0, 1)), ("hybrid", (1, 1))])
def test_mode_invokes_only_selected_branches(mode, counts):
    keyword, vector = Branch("fts"), Branch("vector")
    result = UnifiedRetrievalService(HybridTextbookSearchTool(keyword, vector)).search("query", mode=mode)
    assert (len(keyword.calls), len(vector.calls)) == counts
    assert result.metadata["requested_mode"] == result.metadata["actual_mode"] == mode
    assert len(result.evidence) == 1  # hybrid deduplicates shared hits


def test_unavailable_vector_is_explicit_and_hybrid_degradation_is_audited():
    keyword = Branch("fts")
    service = UnifiedRetrievalService(HybridTextbookSearchTool(keyword))
    assert service.available_modes == ["keyword", "hybrid"]
    result = service.search("query", mode="vector")
    assert result.status == SearchStatus.DISABLED and not result.evidence
    assert not keyword.calls
    result = service.search("query")
    assert result.metadata["actual_mode"] == "keyword" and result.metadata["degraded"]
    service = UnifiedRetrievalService(HybridTextbookSearchTool(keyword, UnavailableVectorTextbookSearchTool("init")))
    assert "vector" not in service.available_modes
    assert service.search("query", mode="vector").status == SearchStatus.ERROR
    result = service.search("query", mode="hybrid")
    assert result.metadata["branch_1_error"] and result.metadata["degraded"]
    assert result.metadata["actual_mode"] == "keyword"


def test_query_failure_is_not_silently_switched_to_keyword():
    keyword, vector = Branch("fts"), Branch("vector", fail=True)
    service = UnifiedRetrievalService(HybridTextbookSearchTool(keyword, vector))
    result = service.search("query", mode="vector")
    assert result.status == SearchStatus.ERROR and result.metadata["actual_mode"] == "none"
    assert keyword.calls == []
    result = service.search("query", mode="hybrid")
    assert result.status == SearchStatus.OK and result.metadata["actual_mode"] == "keyword"


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
def test_raw_scope_requires_all_chunk_constituents_and_maps_provenance(mode):
    chunk = _block("chunk", 0, "bounded evidence").model_copy(update={"source_refs": ["raw1", "raw2"]})
    keyword, vector = Branch("fts", ("chunk", "outside")), Branch("vector", ("chunk", "outside"))
    service = UnifiedRetrievalService(HybridTextbookSearchTool(keyword, vector), blocks=[chunk])
    full = service.search("q", mode=mode, allowed_source_refs={"raw1", "raw2"})
    assert full.evidence[0].source_ref == "raw1"
    assert full.evidence[0].source_refs == ("raw1", "raw2")
    assert len(full.evidence) == 1 and full.filtered_count == 1
    partial = service.search("q", mode=mode, allowed_source_refs={"raw1"})
    assert partial.status == SearchStatus.NO_RESULTS and not partial.evidence
    counts = len(keyword.calls), len(vector.calls)
    assert service.search("q", mode=mode, allowed_source_refs=[]).status == SearchStatus.INVALID_REQUEST
    assert (len(keyword.calls), len(vector.calls)) == counts


@pytest.mark.parametrize("schema,action", [(RetrievalDecision, "search"), (QuizRetrievalDecision, "search"),
                                         (FactCheckRetrievalDecision, "search_textbook")])
def test_decisions_validate_mode(schema, action):
    with pytest.raises(ValidationError):
        schema(action=action, textbook_queries=["q"], textbook_mode="invented")


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
def test_content_quiz_and_fact_check_use_model_mode(monkeypatch, mode):
    from annotation.workflow import context
    from annotation.workflow import run_minimal_workflow
    from annotation.offline import OfflineFixtureProvider
    from annotation.domain.artifacts import InteractiveComponentSandboxReport
    from test_blueprint_loop import _install_source_fixture

    _install_source_fixture(monkeypatch)
    monkeypatch.setenv("RAG_ENABLED", "true")
    instances = []

    class Vector:
        name, version = "test-vector", "test"
        def __init__(self, blocks, **kwargs):
            from annotation.retrieval.tool import InMemoryTextbookSearchTool
            self.tool = InMemoryTextbookSearchTool(blocks)
            instances.append(self)
        def search(self, query, *, limit=5):
            return self.tool.search(query, limit=limit)

    monkeypatch.setattr(context, "LlamaIndexChromaTextbookSearchTool", Vector)
    observed = []
    original = UnifiedRetrievalService.search
    def search(self, query, **kwargs):
        result = original(self, query, **kwargs)
        observed.append((self, kwargs, result))
        return result
    monkeypatch.setattr(UnifiedRetrievalService, "search", search)

    class Provider(OfflineFixtureProvider):
        def generate_structured(self, request):
            name = request.schema.__name__
            if name in {"RetrievalDecision", "QuizRetrievalDecision", "FactCheckRetrievalDecision"}:
                assert "keyword" in request.prompt and "vector" in request.prompt
            response = super().generate_structured(request)
            value = response.value
            if name == "RetrievalDecision":
                value = value.model_copy(update={"textbook_mode": mode})
            elif name == "QuizRetrievalDecision":
                value = request.schema(action="search", textbook_queries=["公理"], textbook_mode=mode)
            elif name == "FactCheckClaimsDraft":
                # A real claim forces the production fact-check retrieval path.
                # Extraction uses deterministic targets, so obtain IDs from the prompt JSON.
                import re
                matches = re.findall(r'"target_id":\s*"([^"\n]+)"', request.prompt)
                if matches:
                    value = request.schema(claims=[{"target_id": matches[-1], "kind": "fact", "text": "公理", "query": "公理"}])
            elif name == "FactCheckRetrievalDecision":
                value = request.schema(action="search_textbook", textbook_queries=["公理"], textbook_mode=mode)
            return replace(response, value=value, raw_text=value.model_dump_json())

    state = run_minimal_workflow(provider=Provider(), run_id="mode-" + mode,
        interactive_component_sandbox_runner=lambda *a, **kw: InteractiveComponentSandboxReport(status="passed"))
    assert observed and len(instances) == 1
    assert all(kwargs.get("mode") == mode for _, kwargs, _ in observed)
    assert len({id(service) for service, _, _ in observed}) == 1
    assert any("allowed_source_refs" in kwargs for _, kwargs, _ in observed)
    assert any(c.metadata["requested_mode"] == mode for t in state["fact_check_artifact"].unit_traces for c in t.retrieval_calls)


def test_vector_context_does_not_append_unrequested_keyword_candidates():
    from annotation.domain.artifacts import LearningBlueprint
    from annotation.workflow.content import build_context_pack
    from test_content_reflection_subgraph import _state
    state = _state()
    unit, task = state["unit"], state["task"]
    chosen = _block("chosen", 0, "semantic paraphrase")
    unrequested = _block("unrequested", 20, unit.title)
    blueprint = LearningBlueprint(artifact_id="bp", run_id=task.run_id, version=1,
        status="accepted", created_by="test", title="test", knowledge_units=[unit])
    keyword, vector = Branch("fts", ("unrequested",)), Branch("vector", ("chosen",))
    service = UnifiedRetrievalService(HybridTextbookSearchTool(keyword, vector), blocks=[chosen, unrequested])
    pack = build_context_pack(task, unit, blueprint, [chosen, unrequested], search_tool=service,
                              textbook_mode="vector", retrieval_queries=["paraphrase"])
    assert pack.source_refs == ["chosen"]
    assert keyword.calls == []
    assert pack.retrieval_calls[0].metadata["actual_mode"] == "vector"
