"""Provider-neutral contracts for bounded fact-check evidence retrieval."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class EvidenceKind(str, Enum):
    """The authority from which an evidence record was retrieved."""

    TEXTBOOK = "textbook"
    WEB = "web"


class SearchStatus(str, Enum):
    """Stable, machine-readable outcomes for every search skill call."""

    OK = "ok"
    NO_RESULTS = "no_results"
    DISABLED = "disabled"
    INVALID_REQUEST = "invalid_request"
    ERROR = "error"


class SearchErrorCode(str, Enum):
    """Stable reasons for non-success search results."""

    EMPTY_QUERY = "empty_query"
    INVALID_LIMIT = "invalid_limit"
    INVALID_TIMEOUT = "invalid_timeout"
    EMPTY_SOURCE_SCOPE = "empty_source_scope"
    EMPTY_DOMAIN_ALLOWLIST = "empty_domain_allowlist"
    INVALID_DOMAIN = "invalid_domain"
    DATABASE_ERROR = "database_error"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_TIMEOUT = "provider_timeout"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    DISABLED_BY_CONFIGURATION = "disabled_by_configuration"


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """A compact, immutable and traceable piece of search evidence."""

    evidence_id: str
    kind: EvidenceKind
    query: str
    text: str
    locator: str | None
    provider: str
    excerpt: str
    text_hash: str
    source_ref: str | None = None
    document_id: str | None = None
    page_number: int | None = None
    block_index: int | None = None
    url: str | None = None
    title: str | None = None
    publisher: str | None = None
    rank: float | None = None


@dataclass(frozen=True, slots=True)
class TextbookSearchRequest:
    """A source-scoped request to the local textbook index."""

    query: str
    allowed_source_refs: frozenset[str] = field(default_factory=frozenset)
    limit: int = 5


@dataclass(frozen=True, slots=True)
class WebSearchRequest:
    """A domain-scoped request to a replaceable web search provider."""

    query: str
    allowed_domains: frozenset[str] = field(default_factory=frozenset)
    limit: int = 3
    timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class WebSearchCandidate:
    """A raw candidate returned by a provider before local policy filtering."""

    url: str
    title: str
    excerpt: str
    publisher: str | None = None
    rank: float | None = None
    content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """The common return type for textbook and web search skills."""

    skill_name: str
    skill_version: str
    query: str
    status: SearchStatus
    available: bool = True
    evidence: tuple[EvidenceRecord, ...] = ()
    error_code: SearchErrorCode | None = None
    error_message: str | None = None
    filtered_count: int = 0
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=dict)


@runtime_checkable
class TextbookDatabaseSearchSkill(Protocol):
    """Search only the current run's allowed textbook source references."""

    name: str
    version: str

    def search(
        self,
        query: str,
        *,
        allowed_source_refs: Iterable[str],
        limit: int = 5,
    ) -> SearchResult: ...


@runtime_checkable
class WebResourceSearchSkill(Protocol):
    """Search a configured set of approved external web domains."""

    name: str
    version: str

    def search(self, query: str, *, limit: int = 3) -> SearchResult: ...


@runtime_checkable
class WebSearchProvider(Protocol):
    """Minimal adapter boundary for a concrete external web search service."""

    name: str
    version: str

    def search(
        self,
        *,
        query: str,
        limit: int,
        timeout_seconds: float,
    ) -> Sequence[WebSearchCandidate]: ...
