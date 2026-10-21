"""Incremental, source-preserving LlamaIndex + persistent Chroma retrieval."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from time import perf_counter

from annotation.domain.artifacts import SourceBlock
from annotation.fact_checking.contracts import (
    EvidenceKind, EvidenceRecord, SearchErrorCode, SearchResult, SearchStatus,
)
from .embeddings import (
    EmbeddingProvider, EmbeddingResponseError, create_embedding_provider_from_env,
    create_llamaindex_embedding, embedding_fingerprint, validate_embeddings,
)


class UnavailableVectorTextbookSearchTool:
    """Preserve an initialization failure as an auditable hybrid branch."""

    name = "llamaindex-chroma-textbook-search"
    version = "v2"

    def __init__(self, error_type: str) -> None:
        self.error_type = error_type

    def search(self, query: str, *, limit: int = 5) -> SearchResult:
        return SearchResult(skill_name=self.name, skill_version=self.version, query=query,
                            status=SearchStatus.ERROR, available=False,
                            error_code=SearchErrorCode.PROVIDER_ERROR,
                            error_message="vector index initialization failed",
                            metadata={"initialization_error": self.error_type})


class LlamaIndexChromaTextbookSearchTool:
    name = "llamaindex-chroma-textbook-search"
    version = "v2"

    def __init__(self, blocks: Iterable[SourceBlock], *, collection_name: str = "annotation-textbook",
                 persist_directory: str = "storage/chroma", embedding_provider: EmbeddingProvider | None = None,
                 document_ids: Iterable[str] | None = None) -> None:
        started = perf_counter()
        try:
            import chromadb
            from chromadb.config import Settings
            from llama_index.core import VectorStoreIndex
            from llama_index.core.schema import TextNode, NodeRelationship, RelatedNodeInfo
            from llama_index.core.vector_stores.utils import node_to_metadata_dict
            from llama_index.vector_stores.chroma import ChromaVectorStore
        except ImportError as exc:
            raise RuntimeError("Install the 'rag' extra to use vector retrieval") from exc

        rows = list(blocks)
        self._blocks = {block.source_ref: block for block in rows}
        if len(self._blocks) != len(rows):
            raise ValueError("duplicate source_ref in textbook snapshot")
        docs = sorted(set(document_ids) if document_ids is not None else {b.document_id for b in rows})
        if any(b.document_id not in docs for b in rows):
            raise ValueError("block is outside document scope")
        self.provider = embedding_provider if embedding_provider is not None else create_embedding_provider_from_env()
        fingerprint = embedding_fingerprint(self.provider)
        scope = hashlib.sha256(json.dumps(docs).encode()).hexdigest()
        prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", collection_name).strip("-_")[:24] or "annotation"
        self.collection_name = f"{prefix}-{scope[:12]}-{fingerprint[:16]}"
        self._client = chromadb.PersistentClient(path=str(persist_directory), settings=Settings(anonymized_telemetry=False))
        self._collection = self._client.get_or_create_collection(
            self.collection_name,
            embedding_function=None,
            metadata={"hnsw:space": "cosine", "embedding_fingerprint": fingerprint,
                      "embedding_identity": json.dumps(self.provider.identity, ensure_ascii=False, sort_keys=True),
                      "document_ids": json.dumps(docs), "schema_version": 2},
        )
        if (self._collection.metadata.get("embedding_fingerprint") != fingerprint
                or self._collection.metadata.get("document_ids") != json.dumps(docs)
                or self._collection.metadata.get("schema_version") != 2):
            raise ValueError("incompatible persisted vector index")
        existing = self._collection.get(include=["metadatas"])
        old = dict(zip(existing["ids"], existing["metadatas"]))
        changed = []
        for block in rows:
            digest = hashlib.sha256(block.text.encode()).hexdigest()
            previous = old.get(block.source_ref) or {}
            if any(previous.get(k) != v for k, v in {
                "content_sha256": digest, "text_hash": block.text_hash,
                "page_number": block.page_number, "block_index": block.block_index,
                "document_id": block.document_id, "parser_version": block.parser_version,
            }.items()):
                changed.append(block)
        stale = sorted(set(old) - set(self._blocks))
        self._dimension = (next(iter(old.values()), None) or {}).get("embedding_dimension")
        bridge = create_llamaindex_embedding(self.provider)
        # Complete encoding/validation before touching persisted rows. An API failure
        # leaves the previous usable snapshot intact, rather than deleting it first.
        vectors = validate_embeddings(bridge.get_text_embedding_batch([b.text for b in changed]),
                                      len(changed), self._dimension) if changed else []
        if vectors:
            self._dimension = len(vectors[0])
        for start in range(0, len(changed), 64):
            batch, embeddings = changed[start:start + 64], vectors[start:start + 64]
            metadatas = []
            for block in batch:
                metadata = {"source_ref": block.source_ref, "document_id": block.document_id,
                            "page_number": block.page_number, "block_index": block.block_index,
                            "text_hash": block.text_hash, "parser_version": block.parser_version,
                            "content_sha256": hashlib.sha256(block.text.encode()).hexdigest(),
                            "embedding_dimension": self._dimension}
                node = TextNode(id_=block.source_ref, text=block.text, metadata=metadata,
                                relationships={NodeRelationship.SOURCE: RelatedNodeInfo(node_id=block.document_id)},
                                excluded_embed_metadata_keys=list(metadata), excluded_llm_metadata_keys=list(metadata))
                metadatas.append(node_to_metadata_dict(node, remove_text=True, flat_metadata=True))
            self._collection.upsert(ids=[b.source_ref for b in batch], embeddings=embeddings,
                                    documents=[b.text for b in batch], metadatas=metadatas)
        if stale:
            self._collection.delete(ids=stale)
        self._index = VectorStoreIndex.from_vector_store(
            ChromaVectorStore(chroma_collection=self._collection), embed_model=bridge,
        )
        self.index_stats = {"indexed": len(changed), "reused": len(rows) - len(changed), "deleted": len(stale),
                            "total": len(rows), "seconds": perf_counter() - started,
                            "collection": self.collection_name, "embedding_fingerprint": fingerprint}

    def search(self, query: str, *, limit: int = 5) -> SearchResult:
        def result(status, *, code=None, message=None, evidence=()):
            return SearchResult(skill_name=self.name, skill_version=self.version, query=normalized,
                                status=status, available=status != SearchStatus.ERROR, evidence=tuple(evidence),
                                error_code=code, error_message=message,
                                metadata={"collection": self.collection_name,
                                          "embedding_fingerprint": embedding_fingerprint(self.provider)})

        normalized = " ".join(query.split()) if isinstance(query, str) else ""
        if not normalized:
            return result(SearchStatus.INVALID_REQUEST, code=SearchErrorCode.EMPTY_QUERY,
                          message="query must contain non-whitespace text")
        if type(limit) is not int or limit < 1:
            return result(SearchStatus.INVALID_REQUEST, code=SearchErrorCode.INVALID_LIMIT,
                          message="limit must be a positive integer")
        if not self._blocks:
            return result(SearchStatus.NO_RESULTS)
        try:
            # Validate queries against the persisted dimension even in a fresh process.
            query_vector = validate_embeddings([self.provider.embed_query(normalized)], 1, self._dimension)[0]
            from llama_index.core.schema import QueryBundle
            retriever = self._index.as_retriever(similarity_top_k=min(limit, 100, len(self._blocks)))
            nodes = retriever.retrieve(QueryBundle(query_str=normalized, embedding=query_vector))
            evidence = []
            seen = set()
            for node in nodes:
                source_ref = node.node.metadata.get("source_ref")
                block = self._blocks.get(source_ref)
                if block is None or source_ref in seen:
                    continue
                if node.node.metadata.get("content_sha256") != hashlib.sha256(block.text.encode()).hexdigest():
                    raise EmbeddingResponseError("persisted source text does not match current snapshot")
                score = float(node.score or 0.0)
                if not math.isfinite(score):
                    raise EmbeddingResponseError("invalid similarity score")
                seen.add(source_ref)
                evidence.append(EvidenceRecord(
                    evidence_id=hashlib.sha256(f"textbook:{source_ref}:{block.text_hash}".encode()).hexdigest()[:24],
                    kind=EvidenceKind.TEXTBOOK, query=normalized, text=block.text, excerpt=block.text,
                    locator=f"page:{block.page_number};block:{block.block_index}", provider=self.name,
                    source_ref=source_ref, document_id=block.document_id, page_number=block.page_number,
                    block_index=block.block_index, text_hash=block.text_hash, rank=score,
                ))
            return result(SearchStatus.OK if evidence else SearchStatus.NO_RESULTS, evidence=evidence)
        except EmbeddingResponseError:
            return result(SearchStatus.ERROR, code=SearchErrorCode.INVALID_PROVIDER_RESPONSE,
                          message="encoder or vector index returned invalid evidence")
        except Exception as exc:
            from openai import APITimeoutError
            code = SearchErrorCode.PROVIDER_TIMEOUT if isinstance(exc, (TimeoutError, APITimeoutError)) else SearchErrorCode.PROVIDER_ERROR
            return result(SearchStatus.ERROR, code=code, message="vector retrieval failed; check encoder and index configuration")
