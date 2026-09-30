"""Provider-neutral textbook retrieval tools used by content generation."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from annotation.domain.artifacts import SourceBlock
from annotation.fact_checking.contracts import (
    EvidenceKind,
    EvidenceRecord,
    SearchResult,
    SearchStatus,
    TextbookSearchTool,
    UnifiedRetrievalTool,
    WebResourceSearchSkill,
    SearchResult,
    SearchStatus,
)


class InMemoryTextbookSearchTool:
    """Deterministic adapter with the same contract as SQLite/vector tools.

    The workflow uses this adapter when source blocks are already in memory
    (for example during a single run or in tests). SQLite FTS5 remains the
    persistent adapter; a future vector adapter can implement the same
    ``TextbookSearchTool`` protocol without changing the content agent.
    """

    name = "in-memory-textbook-search"
    version = "v1"

    def __init__(self, blocks: Iterable[SourceBlock]) -> None:
        self._blocks = tuple(blocks)

    def search(self, query: str, *, limit: int = 5) -> SearchResult:
        normalized = " ".join(query.split()) if isinstance(query, str) else ""
        if not normalized:
            return SearchResult(
                skill_name=self.name,
                skill_version=self.version,
                query="",
                status=SearchStatus.INVALID_REQUEST,
            )
        terms = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}|\d+", normalized)
        scored: list[tuple[int, SourceBlock]] = []
        for block in self._blocks:
            score = sum(block.text.count(term) for term in terms) if terms else 0
            if score:
                scored.append((score, block))
        scored.sort(key=lambda item: (-item[0], item[1].page_number, item[1].block_index))
        evidence = tuple(
            EvidenceRecord(
                evidence_id=hashlib.sha256(f"{block.source_ref}:{block.text_hash}".encode()).hexdigest()[:24],
                kind=EvidenceKind.TEXTBOOK,
                query=normalized,
                text=block.text,
                excerpt=block.text,
                locator=f"page:{block.page_number};block:{block.block_index}",
                provider=self.name,
                source_ref=block.source_ref,
                document_id=block.document_id,
                page_number=block.page_number,
                block_index=block.block_index,
                text_hash=block.text_hash,
                rank=float(-score),
            )
            for score, block in scored[: max(1, min(limit, 100))]
        )
        return SearchResult(
            skill_name=self.name,
            skill_version=self.version,
            query=normalized,
            status=SearchStatus.OK if evidence else SearchStatus.NO_RESULTS,
            evidence=evidence,
        )


class UnifiedRetrievalService:
    """Routes textbook and optional web searches through one interface.

    Content generation and fact checking can share this service while keeping
    web access explicitly opt-in. It does not merge or rerank results; callers
    receive the provider's bounded evidence records unchanged.
    """

    def __init__(
        self,
        textbook: TextbookSearchTool,
        web: WebResourceSearchSkill | None = None,
    ) -> None:
        self.textbook = textbook
        self.web = web

    def search(self, query: str, *, source: str = "textbook", limit: int = 5):
        if source == "textbook":
            return self.textbook.search(query, limit=limit)
        if source == "web":
            if self.web is None:
                from annotation.fact_checking import DisabledWebResourceSearchSkill

                return DisabledWebResourceSearchSkill().search(query, limit=limit)
            return self.web.search(query, limit=limit)
        raise ValueError("source must be 'textbook' or 'web'")


class HybridTextbookSearchTool:
    """Combines keyword and vector evidence without reranking."""

    name = "hybrid-textbook-search"
    version = "v1"

    def __init__(self, keyword_tool: TextbookSearchTool, vector_tool: TextbookSearchTool | None = None) -> None:
        self.keyword_tool = keyword_tool
        self.vector_tool = vector_tool

    def search(self, query: str, *, limit: int = 5) -> SearchResult:
        results = [self.keyword_tool.search(query, limit=limit)]
        if self.vector_tool is not None:
            results.append(self.vector_tool.search(query, limit=limit))
        evidence = []
        seen: set[str] = set()
        for result in results:
            for item in result.evidence:
                key = item.source_ref or item.evidence_id
                if key not in seen:
                    seen.add(key)
                    evidence.append(item)
                if len(evidence) >= limit * 2:
                    break
        return SearchResult(
            skill_name=self.name,
            skill_version=self.version,
            query=query,
            status=SearchStatus.OK if evidence else SearchStatus.NO_RESULTS,
            evidence=tuple(evidence[: max(1, min(limit, 100))]),
            metadata={"keyword": True, "vector": self.vector_tool is not None, "reranker": False},
        )


__all__ = ["HybridTextbookSearchTool", "InMemoryTextbookSearchTool", "UnifiedRetrievalService"]
