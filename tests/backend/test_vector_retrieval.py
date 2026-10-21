from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from annotation.domain.artifacts import SourceBlock
from annotation.fact_checking.contracts import EvidenceKind, EvidenceRecord, SearchErrorCode, SearchResult, SearchStatus
from annotation.retrieval.embeddings import (
    EmbeddingResponseError, OpenAICompatibleEmbeddingProvider, create_llamaindex_embedding,
    validate_embeddings,
)
from annotation.retrieval.llamaindex_chroma import LlamaIndexChromaTextbookSearchTool
from annotation.retrieval.tool import HybridTextbookSearchTool


class Encoder:
    name = "deterministic-test-encoder"

    def __init__(self, model="test-v1"):
        self.model = model
        self.documents = []
        self.queries = []
        self.fail = False

    @property
    def identity(self):
        return {"model": self.model}

    def embed_documents(self, texts):
        if self.fail:
            raise TimeoutError("test timeout")
        self.documents.extend(texts)
        return [[1.0, 0.2] if "上确界" in text else [0.1, 1.0] for text in texts]

    def embed_query(self, text):
        if self.fail:
            raise TimeoutError("test timeout")
        self.queries.append(text)
        return [1.0, 0.2]


@pytest.fixture
def rag_dependencies():
    pytest.importorskip("chromadb")
    pytest.importorskip("llama_index.core")
    pytest.importorskip("llama_index.vector_stores.chroma")


def block(text="上确界由完全公理保证存在", ref="s1", doc="d1", page=2):
    return SourceBlock(artifact_id=ref, source_ref=ref, document_id=doc, run_id="test", version=1,
                       status="draft", source_refs=[ref], created_by="test", page_number=page,
                       block_index=3, text=text, text_hash=hashlib.sha256(text.encode()).hexdigest(),
                       parser_version="test", bbox=(0, 0, 1, 1))


def test_persistent_index_reuses_updates_deletes_and_preserves_source(tmp_path, rag_dependencies):
    provider = Encoder()
    a, b = block(), block("复数的实部和虚部", "s2")
    kwargs = {"persist_directory": str(tmp_path), "embedding_provider": provider}
    first = LlamaIndexChromaTextbookSearchTool([a, b], **kwargs)
    assert first.index_stats["indexed"] == 2
    assert provider.documents == [a.text, b.text]  # No metadata or query instruction in documents.
    result = first.search("最小的上界")
    assert result.status == SearchStatus.OK
    hit = result.evidence[0]
    assert (hit.source_ref, hit.text, hit.text_hash, hit.page_number, hit.block_index) == (
        a.source_ref, a.text, a.text_hash, a.page_number, a.block_index)
    assert provider.queries == ["最小的上界"]
    second = LlamaIndexChromaTextbookSearchTool([a, b], **kwargs)
    assert second.index_stats["reused"] == 2
    assert len(provider.documents) == 2
    updated = block("上确界的定义已更新")
    third = LlamaIndexChromaTextbookSearchTool([updated], **kwargs)
    assert third.index_stats["indexed"] == 1
    assert third.index_stats["deleted"] == 1
    assert third._collection.count() == 1
    assert third.search("上界").evidence[0].text == updated.text
    assert first.search("上界").error_code == SearchErrorCode.INVALID_PROVIDER_RESPONSE
    empty = LlamaIndexChromaTextbookSearchTool([], document_ids=["d1"], **kwargs)
    assert empty.index_stats["deleted"] == 1
    assert empty.search("上界").status == SearchStatus.NO_RESULTS


def test_model_and_document_isolation(tmp_path, rag_dependencies):
    first = LlamaIndexChromaTextbookSearchTool([block()], persist_directory=str(tmp_path), embedding_provider=Encoder())
    other_model = LlamaIndexChromaTextbookSearchTool([block()], persist_directory=str(tmp_path), embedding_provider=Encoder("v2"))
    other_book = LlamaIndexChromaTextbookSearchTool([block(doc="d2")], persist_directory=str(tmp_path), embedding_provider=Encoder())
    assert len({x.collection_name for x in [first, other_model, other_book]}) == 3
    assert first.search("上确界").evidence[0].document_id == "d1"
    assert other_book.search("上确界").evidence[0].document_id == "d2"


def test_failed_index_update_preserves_existing_data(tmp_path, rag_dependencies):
    encoder = Encoder()
    kwargs = {"persist_directory": str(tmp_path), "embedding_provider": encoder}
    first = LlamaIndexChromaTextbookSearchTool([block()], **kwargs)
    encoder.fail = True
    with pytest.raises(TimeoutError):
        LlamaIndexChromaTextbookSearchTool([block("新的上确界")], **kwargs)
    assert first._collection.get(include=["documents"])["documents"] == [block().text]
    assert first.search("上确界").error_code == SearchErrorCode.PROVIDER_TIMEOUT


@pytest.mark.parametrize("query,limit,code", [(" ", 5, SearchErrorCode.EMPTY_QUERY),
    (None, 5, SearchErrorCode.EMPTY_QUERY), ("界", 0, SearchErrorCode.INVALID_LIMIT),
    ("界", True, SearchErrorCode.INVALID_LIMIT), ("界", 1.5, SearchErrorCode.INVALID_LIMIT)])
def test_vector_validates_requests(tmp_path, query, limit, code, rag_dependencies):
    tool = LlamaIndexChromaTextbookSearchTool([], persist_directory=str(tmp_path), embedding_provider=Encoder())
    result = tool.search(query, limit=limit)
    assert result.status == SearchStatus.INVALID_REQUEST
    assert result.error_code == code


@pytest.mark.parametrize("values,count", [([], 1), ([[1, 2], [1]], 2), ([[float('nan')]], 1),
    ([[float('inf')]], 1), ([[0, 0]], 1), ([[True]], 1), ([["1"]], 1)])
def test_rejects_invalid_embeddings(values, count):
    with pytest.raises(EmbeddingResponseError):
        validate_embeddings(values, count)


def api_client(responses):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=next(responses))
    return SimpleNamespace(embeddings=SimpleNamespace(create=create)), calls


def item(index, values):
    return SimpleNamespace(index=index, embedding=values)


def test_api_batching_ordering_dimension_and_no_credentials_in_identity():
    client, calls = api_client(iter([[item(1, [0, 1]), item(0, [1, 0])], [item(0, [1, 2])]]))
    provider = OpenAICompatibleEmbeddingProvider(model="bge-m3", api_key="secret", client=client, batch_size=2)
    assert provider.embed_documents(["a", "b", "c"]) == [[1, 0], [0, 1], [1, 2]]
    assert [x["input"] for x in calls] == [["a", "b"], ["c"]]
    assert "secret" not in str(provider.identity)
    provider._client, _ = api_client(iter([[item(0, [1, 2, 3])]]))
    with pytest.raises(EmbeddingResponseError):
        provider.embed_query("a")


@pytest.mark.parametrize("items", [[item(0, [1]), item(0, [2])], [], [item(2, [1])], [item(True, [1])]])
def test_api_rejects_missing_duplicate_indices(items):
    client, _ = api_client(iter([items]))
    provider = OpenAICompatibleEmbeddingProvider(model="test", api_key="test", client=client)
    with pytest.raises(EmbeddingResponseError):
        provider.embed_documents(["a"])


def test_llamaindex_bridge_keeps_query_and_document_roles():
    pytest.importorskip("llama_index.core")
    encoder = Encoder()
    bridge = create_llamaindex_embedding(encoder)
    bridge.get_text_embedding_batch(["document"])
    bridge.get_query_embedding("query")
    assert encoder.documents == ["document"]
    assert encoder.queries == ["query"]


def evidence(ref):
    return EvidenceRecord(evidence_id=ref, kind=EvidenceKind.TEXTBOOK, query="q", text=ref, locator=ref,
                          provider="test", excerpt=ref, text_hash=ref, source_ref=ref)


class SearchTool:
    name, version = "test", "v1"
    def __init__(self, refs=(), status=SearchStatus.OK, raises=False):
        self.refs, self.status, self.raises = refs, status, raises
    def search(self, query, *, limit=5):
        if self.raises:
            raise RuntimeError("test failure")
        return SearchResult(skill_name=self.name, skill_version=self.version, query=query,
                            status=self.status, evidence=tuple(evidence(r) for r in self.refs[:limit]))


def test_hybrid_alternates_deduplicates_and_does_not_starve_vectors():
    tool = HybridTextbookSearchTool(SearchTool(["a", "b", "c", "d"]), SearchTool(["b", "v", "a", "w"]))
    assert [x.source_ref for x in tool.search("q", limit=4).evidence] == ["a", "b", "v", "c"]
    assert tool.search("q").metadata["degraded"] is False


@pytest.mark.parametrize("raises", [True, False])
def test_hybrid_preserves_partial_failures(raises):
    tool = HybridTextbookSearchTool(SearchTool(status=SearchStatus.ERROR, raises=raises), SearchTool(["v"]))
    result = tool.search("q")
    assert result.status == SearchStatus.OK
    assert result.metadata["degraded"] is True
    assert result.evidence[0].source_ref == "v"
    empty = HybridTextbookSearchTool(SearchTool(status=SearchStatus.ERROR), SearchTool(status=SearchStatus.NO_RESULTS)).search("q")
    assert empty.status == SearchStatus.NO_RESULTS and empty.metadata["degraded"]
    failed = HybridTextbookSearchTool(SearchTool(status=SearchStatus.ERROR), SearchTool(raises=True)).search("q")
    assert failed.status == SearchStatus.ERROR and not failed.available


def test_auto_provider_selects_once_without_reusing_generation_credentials(monkeypatch):
    import annotation.retrieval.embeddings as module
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "auto")
    monkeypatch.delenv("RAG_EMBEDDING_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_API_KEY", "must-not-be-used")
    monkeypatch.setattr(module, "LocalSentenceTransformerEmbeddingProvider", lambda *a, **k: Encoder())
    assert isinstance(module.create_embedding_provider_from_env(), Encoder)
    monkeypatch.setenv("RAG_EMBEDDING_API_KEY", "explicit-embedding-key")
    class BrokenAPI:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "explicit-embedding-key"
        def embed_query(self, text):
            raise TimeoutError()
    monkeypatch.setattr(module, "OpenAICompatibleEmbeddingProvider", BrokenAPI)
    assert isinstance(module.create_embedding_provider_from_env(), Encoder)
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "api")
    assert isinstance(module.create_embedding_provider_from_env(), BrokenAPI)


def test_local_encoder_forces_cpu_and_only_prefixes_queries(monkeypatch):
    import sys
    import types
    from annotation.retrieval.embeddings import LocalSentenceTransformerEmbeddingProvider, BGE_QUERY_INSTRUCTION
    captured = []
    class Model:
        max_seq_length = 512
        def __getitem__(self, key):
            return SimpleNamespace(auto_model=SimpleNamespace(config=SimpleNamespace(_commit_hash="test-revision")))
        def tokenizer(self, texts, **kwargs):
            return {"input_ids": [[1] * (len(t) + 2) for t in texts]}
        def encode(self, texts, **kwargs):
            captured.append((texts, kwargs))
            return [[1., 0.] for _ in texts]
    def sentence_transformer(path, **kwargs):
        assert kwargs["device"] == "cpu" and kwargs["trust_remote_code"] is False
        return Model()
    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=sentence_transformer))
    encoder = LocalSentenceTransformerEmbeddingProvider()
    encoder.embed_documents(["原始教材"])
    encoder.embed_query("提问")
    assert captured[0][0] == ["原始教材"]
    assert captured[1][0] == [BGE_QUERY_INSTRUCTION + "提问"]
    with pytest.raises(ValueError, match="token limit"):
        encoder.embed_documents(["x" * 512])


def test_real_sdk_retries_are_bounded_and_timeout_configured():
    import httpx
    from openai import OpenAI, APITimeoutError
    calls = []
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("test timeout", request=request)
    client = OpenAI(api_key="not-a-real-secret", base_url="https://embedding.test/v1", max_retries=1,
                    timeout=0.1, http_client=httpx.Client(transport=httpx.MockTransport(timeout)))
    encoder = OpenAICompatibleEmbeddingProvider(model="test", api_key="test", client=client)
    with pytest.raises(APITimeoutError):
        encoder.embed_query("query")
    assert len(calls) == 2
    client.close()


def test_real_sdk_rate_limit_recovery():
    import httpx
    from openai import OpenAI
    calls = []
    def response(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after-ms": "1"}, json={"error": {"message": "rate limit"}})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1., 0.], "object": "embedding"}],
                                         "model": "test", "object": "list", "usage": {"prompt_tokens": 1, "total_tokens": 1}})
    client = OpenAI(api_key="test", base_url="https://embedding.test/v1", max_retries=1,
                    http_client=httpx.Client(transport=httpx.MockTransport(response)))
    encoder = OpenAICompatibleEmbeddingProvider(model="test", api_key="test", client=client)
    assert encoder.embed_query("query") == [1., 0.]
    assert len(calls) == 2
    client.close()
