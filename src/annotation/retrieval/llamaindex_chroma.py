"""Optional LlamaIndex + Chroma textbook retriever.

Imports are lazy so the base Annotation install remains FTS-only. Install the
``rag`` extra before constructing this adapter.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from annotation.domain.artifacts import SourceBlock
from annotation.fact_checking.contracts import EvidenceKind, EvidenceRecord, SearchResult, SearchStatus


class LlamaIndexChromaTextbookSearchTool:
    name = "llamaindex-chroma-textbook-search"
    version = "v1"

    def __init__(self, blocks: Iterable[SourceBlock], *, collection_name: str = "annotation-textbook", persist_directory: str = "storage/chroma") -> None:
        try:
            import chromadb
            from llama_index.core import Document, VectorStoreIndex
            from llama_index.core.storage.storage_context import StorageContext
            from llama_index.vector_stores.chroma import ChromaVectorStore
            from annotation.retrieval.embeddings import create_llamaindex_embedding_from_env
        except ImportError as exc:
            raise RuntimeError("Install the 'rag' extra to use LlamaIndexChromaTextbookSearchTool") from exc

        self._blocks = {block.source_ref: block for block in blocks}
        client = chromadb.PersistentClient(path=persist_directory)
        collection = client.get_or_create_collection(collection_name)
        vector_store = ChromaVectorStore(chroma_collection=collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        documents = [
            Document(
                text=block.text,
                doc_id=block.source_ref,
                metadata={
                    "source_ref": block.source_ref,
                    "document_id": block.document_id,
                    "page_number": block.page_number,
                    "block_index": block.block_index,
                    "text_hash": block.text_hash,
                },
            )
            for block in self._blocks.values()
        ]
        self._index = VectorStoreIndex.from_documents(
            documents,
            storage_context=storage_context,
            embed_model=create_llamaindex_embedding_from_env(),
        )

    def search(self, query: str, *, limit: int = 5) -> SearchResult:
        if not isinstance(query, str) or not query.strip():
            return SearchResult(skill_name=self.name, skill_version=self.version, query="", status=SearchStatus.INVALID_REQUEST)
        retriever = self._index.as_retriever(similarity_top_k=max(1, min(limit, 100)))
        nodes = retriever.retrieve(query)
        evidence = []
        for node in nodes:
            metadata = node.node.metadata
            source_ref = metadata.get("source_ref")
            text = node.node.get_content()
            if not source_ref:
                continue
            evidence.append(EvidenceRecord(
                evidence_id=hashlib.sha256(f"{source_ref}:{metadata.get('text_hash', '')}".encode()).hexdigest()[:24],
                kind=EvidenceKind.TEXTBOOK,
                query=query,
                text=text,
                excerpt=text,
                locator=f"page:{metadata.get('page_number')};block:{metadata.get('block_index')}",
                provider=self.name,
                source_ref=source_ref,
                document_id=metadata.get("document_id"),
                page_number=int(metadata["page_number"]) if metadata.get("page_number") is not None else None,
                block_index=int(metadata["block_index"]) if metadata.get("block_index") is not None else None,
                text_hash=str(metadata.get("text_hash", "")),
                rank=float(getattr(node, "score", 0.0) or 0.0),
            ))
        return SearchResult(
            skill_name=self.name,
            skill_version=self.version,
            query=query,
            status=SearchStatus.OK if evidence else SearchStatus.NO_RESULTS,
            evidence=tuple(evidence),
        )
