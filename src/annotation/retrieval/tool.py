"""Provider-neutral textbook retrieval tools used by content generation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from collections.abc import Iterable
from itertools import zip_longest

from annotation.domain.artifacts import SourceBlock
from annotation.fact_checking.contracts import (
    EvidenceKind,
    EvidenceRecord,
    SearchResult,
    SearchStatus,
    TextbookSearchTool,
    UnifiedRetrievalTool,
    WebResourceSearchSkill,
    SearchErrorCode,
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
    """Route explicit textbook modes and optional web searches with source auditing."""

    def __init__(
        self,
        textbook: TextbookSearchTool,
        web: WebResourceSearchSkill | None = None,
        *,
        blocks: Iterable[SourceBlock] = (),
    ) -> None:
        self.textbook = textbook
        self.web = web
        self._source_refs = {b.source_ref: tuple(b.source_refs or [b.source_ref]) for b in blocks}

    @property
    def available_modes(self) -> list[str]:
        modes = ["keyword", "hybrid"]
        vector = self.textbook.vector_tool if isinstance(self.textbook, HybridTextbookSearchTool) else None
        if vector is not None and not hasattr(vector, "error_type"):
            modes.insert(1, "vector")
        return modes

    def search(self, query: str, *, source: str = "textbook", limit: int = 5,
               mode: str = "hybrid", allowed_source_refs: Iterable[str] | None = None) -> SearchResult:
        if source == "web":
            if self.web is None:
                from annotation.fact_checking import DisabledWebResourceSearchSkill
                return DisabledWebResourceSearchSkill().search(query, limit=limit)
            return self.web.search(query, limit=limit)
        if source != "textbook":
            raise ValueError("source must be 'textbook' or 'web'")
        if mode not in {"keyword", "vector", "hybrid"}:
            raise ValueError("mode must be 'keyword', 'vector' or 'hybrid'")
        scope = None if allowed_source_refs is None else set(allowed_source_refs)
        if scope == set():
            return SearchResult(skill_name="textbook-search", skill_version="v1", query=query,
                                status=SearchStatus.INVALID_REQUEST, error_code=SearchErrorCode.EMPTY_SOURCE_SCOPE,
                                metadata={"requested_mode": mode, "actual_mode": "none"})
        hybrid = self.textbook if isinstance(self.textbook, HybridTextbookSearchTool) else None
        tool = self.textbook
        if mode == "keyword" and hybrid:
            tool = hybrid.keyword_tool
        elif mode == "vector":
            tool = hybrid.vector_tool if hybrid else None
        if tool is None:
            return SearchResult(skill_name="textbook-search", skill_version="v1", query=query,
                                status=SearchStatus.DISABLED, available=False,
                                error_code=SearchErrorCode.DISABLED_BY_CONFIGURATION,
                                error_message="vector retrieval is not enabled",
                                metadata={"requested_mode": mode, "actual_mode": "none"})
        try:
            result = tool.search(query, limit=limit)
        except Exception as exc:
            result = SearchResult(skill_name=tool.name, skill_version=tool.version, query=query,
                                  status=SearchStatus.ERROR, available=False,
                                  error_code=SearchErrorCode.PROVIDER_ERROR,
                                  error_message=f"textbook retrieval failed: {type(exc).__name__}")
        actual = mode
        if mode == "hybrid":
            if not hybrid or hybrid.vector_tool is None:
                actual = "keyword"
            elif result.metadata.get("degraded"):
                actual = ("keyword" if result.metadata.get("branch_0_status") in {"ok", "no_results"}
                          else "vector") if result.available else "none"
        if result.status not in {SearchStatus.OK, SearchStatus.NO_RESULTS}:
            actual = "none"
        evidence = []
        for item in result.evidence:
            refs = self._source_refs.get(item.source_ref, item.source_refs or ((item.source_ref,) if item.source_ref else ()))
            if scope is not None:
                if not refs or None in refs or not set(refs).issubset(scope):
                    continue
                item = replace(item, source_ref=refs[0])
            evidence.append(replace(item, source_refs=tuple(refs)))
        status = result.status
        if status == SearchStatus.OK and not evidence:
            status = SearchStatus.NO_RESULTS
        return replace(result, evidence=tuple(evidence), status=status,
                       filtered_count=result.filtered_count + len(result.evidence) - len(evidence),
                       metadata={**result.metadata, "requested_mode": mode, "actual_mode": actual,
                                 "degraded": actual != mode and mode == "hybrid"})


class HybridTextbookSearchTool:
    """Combines keyword and vector evidence without reranking."""

    name = "hybrid-textbook-search"
    version = "v2"

    def __init__(self, keyword_tool: TextbookSearchTool, vector_tool: TextbookSearchTool | None = None) -> None:
        self.keyword_tool = keyword_tool
        self.vector_tool = vector_tool

    def search(self, query: str, *, limit: int = 5) -> SearchResult:
        normalized = " ".join(query.split()) if isinstance(query, str) else ""
        if not normalized or type(limit) is not int or limit < 1:
            return SearchResult(skill_name=self.name, skill_version=self.version, query=normalized,
                                status=SearchStatus.INVALID_REQUEST,
                                error_code=SearchErrorCode.EMPTY_QUERY if not normalized else SearchErrorCode.INVALID_LIMIT)
        limit = min(limit, 100)
        results = []
        for tool in [self.keyword_tool, self.vector_tool]:
            if tool is None:
                continue
            try:
                results.append(tool.search(normalized, limit=limit))
            except Exception:
                results.append(SearchResult(skill_name=tool.name, skill_version=tool.version,
                                            query=normalized, status=SearchStatus.ERROR, available=False,
                                            error_code=SearchErrorCode.PROVIDER_ERROR))
        valid = [r for r in results if r.status in {SearchStatus.OK, SearchStatus.NO_RESULTS}]
        evidence = []
        seen: set[str] = set()
        for pair in zip_longest(*(r.evidence for r in valid)):
            for item in pair:
                if item is None:
                    continue
                key = item.source_ref or item.evidence_id
                if key not in seen:
                    seen.add(key)
                    evidence.append(item)
        return SearchResult(
            skill_name=self.name,
            skill_version=self.version,
            query=normalized,
            status=(SearchStatus.OK if evidence else SearchStatus.NO_RESULTS) if valid else SearchStatus.ERROR,
            available=bool(valid),
            error_code=None if valid else SearchErrorCode.PROVIDER_ERROR,
            error_message=None if valid else "all textbook retrieval branches failed",
            evidence=tuple(evidence[:limit]),
            metadata={"keyword": True, "vector": self.vector_tool is not None, "reranker": False,
                      "fusion": "alternating-keyword-first", "degraded": len(valid) != len(results),
                      **{f"branch_{i}_status": r.status.value for i, r in enumerate(results)},
                      **{f"branch_{i}_error": r.error_code.value for i, r in enumerate(results) if r.error_code},
                      **{f"branch_{i}_{key}": value for i, r in enumerate(results) for key, value in r.metadata.items()}},
        )


__all__ = ["HybridTextbookSearchTool", "InMemoryTextbookSearchTool", "UnifiedRetrievalService"]
