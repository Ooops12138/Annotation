"""Application-internal, bounded search skills for A-003 fact checking."""

from .contracts import (
    EvidenceKind,
    EvidenceRecord,
    SearchErrorCode,
    SearchResult,
    SearchStatus,
    TextbookDatabaseSearchSkill,
    TextbookSearchRequest,
    WebResourceSearchSkill,
    WebSearchCandidate,
    WebSearchProvider,
    WebSearchRequest,
)
from .skills import (
    DeterministicMockWebResourceSearchSkill,
    DeterministicMockWebSearchProvider,
    DisabledWebResourceSearchSkill,
    ProviderBackedWebResourceSearchSkill,
    SQLiteFts5TextbookDatabaseSearchSkill,
    normalize_allowed_domain,
    normalize_allowed_domains,
    normalize_https_url,
)

__all__ = [
    "DeterministicMockWebResourceSearchSkill",
    "DeterministicMockWebSearchProvider",
    "DisabledWebResourceSearchSkill",
    "EvidenceKind",
    "EvidenceRecord",
    "ProviderBackedWebResourceSearchSkill",
    "SQLiteFts5TextbookDatabaseSearchSkill",
    "SearchErrorCode",
    "SearchResult",
    "SearchStatus",
    "TextbookDatabaseSearchSkill",
    "TextbookSearchRequest",
    "WebResourceSearchSkill",
    "WebSearchCandidate",
    "WebSearchProvider",
    "WebSearchRequest",
    "normalize_allowed_domain",
    "normalize_allowed_domains",
    "normalize_https_url",
]
