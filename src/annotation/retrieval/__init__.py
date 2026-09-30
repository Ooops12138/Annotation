"""Traceable local retrieval adapters."""

from .fts import SourceSearchResult, index_source_blocks, initialize_fts, search_source_blocks
from .tool import HybridTextbookSearchTool, InMemoryTextbookSearchTool, UnifiedRetrievalService
from .llamaindex_chroma import LlamaIndexChromaTextbookSearchTool

__all__ = [
    "SourceSearchResult",
    "index_source_blocks",
    "initialize_fts",
    "search_source_blocks",
    "InMemoryTextbookSearchTool",
    "HybridTextbookSearchTool",
    "UnifiedRetrievalService",
    "LlamaIndexChromaTextbookSearchTool",
]
