"""Traceable local retrieval adapters."""

from .fts import SourceSearchResult, index_source_blocks, initialize_fts, search_source_blocks

__all__ = [
    "SourceSearchResult",
    "index_source_blocks",
    "initialize_fts",
    "search_source_blocks",
]
