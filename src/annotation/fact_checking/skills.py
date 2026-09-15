"""Bounded, policy-enforcing implementations of fact-check search skills."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from annotation.retrieval.fts import search_source_blocks

from .contracts import (
    EvidenceKind,
    EvidenceRecord,
    SearchErrorCode,
    SearchResult,
    SearchStatus,
    TextbookDatabaseSearchSkill,
    WebResourceSearchSkill,
    WebSearchCandidate,
    WebSearchProvider,
)


DatabaseTarget = str | Path | sqlite3.Connection

_MAX_FTS_CANDIDATES = 100
_MAX_SQL_VARIABLES = 900
_FTS_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalized_query(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _fts_safe_query(query: str) -> str | None:
    """Keep arbitrary claim text from becoming malformed FTS5 syntax."""

    tokens = _FTS_TOKEN.findall(query)
    if not tokens:
        return None
    operators = {"AND", "OR", "NOT", "NEAR"}
    if all(token.upper() not in operators for token in tokens):
        return " ".join(tokens)
    return " ".join(f'"{token}"' for token in tokens)


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _compact_excerpt(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 3)].rstrip()}..."


def normalize_allowed_domain(value: object) -> str | None:
    """Return a canonical hostname or ``None`` for an unsafe allowlist value."""

    if not isinstance(value, str):
        return None
    candidate = value.strip().lower().rstrip(".")
    if not candidate or any(character.isspace() for character in candidate):
        return None
    if any(marker in candidate for marker in (":", "/", "@", "?", "#")):
        return None
    try:
        candidate = candidate.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(candidate) > 253 or any(not _DOMAIN_LABEL.fullmatch(label) for label in candidate.split(".")):
        return None
    return candidate


def normalize_allowed_domains(values: Iterable[object]) -> frozenset[str]:
    """Canonicalize a configured domain list and reject malformed entries."""

    normalized: set[str] = set()
    for value in values:
        domain = normalize_allowed_domain(value)
        if domain is None:
            raise ValueError("invalid_domain")
        normalized.add(domain)
    return frozenset(normalized)


def normalize_https_url(url: object, allowed_domains: Iterable[object]) -> str | None:
    """Return a canonical allowed HTTPS URL, otherwise reject it."""

    if not isinstance(url, str) or not url or any(ord(character) < 32 for character in url):
        return None
    try:
        allowed = normalize_allowed_domains(allowed_domains)
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except (ValueError, UnicodeError):
        return None
    if parts.scheme.lower() != "https" or not parts.netloc or parts.username or parts.password:
        return None
    host = normalize_allowed_domain(hostname)
    if host is None or not any(host == domain or host.endswith(f".{domain}") for domain in allowed):
        return None
    netloc = host if port in (None, 443) else f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit(("https", netloc, path, parts.query, ""))


def _invalid_result(
    *,
    name: str,
    version: str,
    query: str,
    code: SearchErrorCode,
    message: str,
) -> SearchResult:
    return SearchResult(
        skill_name=name,
        skill_version=version,
        query=query,
        status=SearchStatus.INVALID_REQUEST,
        error_code=code,
        error_message=message,
    )


def _error_result(
    *,
    name: str,
    version: str,
    query: str,
    code: SearchErrorCode,
    message: str,
    available: bool = True,
) -> SearchResult:
    return SearchResult(
        skill_name=name,
        skill_version=version,
        query=query,
        status=SearchStatus.ERROR,
        available=available,
        error_code=code,
        error_message=message,
    )


def _validated_limit(value: object, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return min(value, maximum)


def _text_hashes(database: DatabaseTarget, source_refs: Sequence[str]) -> dict[str, str]:
    """Load metadata absent from ``SourceSearchResult`` without changing FTS."""

    if not source_refs:
        return {}
    if isinstance(database, sqlite3.Connection):
        connection = database
        close_when_done = False
    else:
        connection = sqlite3.connect(str(database))
        close_when_done = True
    try:
        hashes: dict[str, str] = {}
        for offset in range(0, len(source_refs), _MAX_SQL_VARIABLES):
            chunk = source_refs[offset : offset + _MAX_SQL_VARIABLES]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"SELECT source_ref, text_hash FROM source_blocks WHERE source_ref IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            hashes.update({str(row[0]): str(row[1]) for row in rows})
        return hashes
    finally:
        if close_when_done:
            connection.close()


class SQLiteFts5TextbookDatabaseSearchSkill:
    """Source-scoped adapter over the project's existing local SQLite FTS5 index."""

    name = "textbook-database-search"
    version = "v1"

    def __init__(
        self,
        database: DatabaseTarget,
        *,
        max_results: int = 5,
        excerpt_char_limit: int = 1_000,
    ) -> None:
        if max_results < 1 or max_results > _MAX_FTS_CANDIDATES:
            raise ValueError("max_results must be between 1 and 100")
        if excerpt_char_limit < 32:
            raise ValueError("excerpt_char_limit must be at least 32")
        self._database = database
        self._max_results = max_results
        self._excerpt_char_limit = excerpt_char_limit

    def search(
        self,
        query: str,
        *,
        allowed_source_refs: Iterable[str],
        limit: int = 5,
    ) -> SearchResult:
        query = _normalized_query(query)
        if query is None:
            return _invalid_result(
                name=self.name,
                version=self.version,
                query="",
                code=SearchErrorCode.EMPTY_QUERY,
                message="query must contain non-whitespace text",
            )
        if isinstance(allowed_source_refs, (str, bytes)):
            return _invalid_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.EMPTY_SOURCE_SCOPE,
                message="allowed_source_refs must be an iterable of source references",
            )
        try:
            allowed_scope = frozenset(
                source_ref.strip()
                for source_ref in allowed_source_refs
                if isinstance(source_ref, str) and source_ref.strip()
            )
        except TypeError:
            return _invalid_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.EMPTY_SOURCE_SCOPE,
                message="allowed_source_refs must be an iterable of source references",
            )
        if not allowed_scope:
            return _invalid_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.EMPTY_SOURCE_SCOPE,
                message="allowed_source_refs must contain at least one source reference",
            )
        effective_limit = _validated_limit(limit, self._max_results)
        if effective_limit is None:
            return _invalid_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.INVALID_LIMIT,
                message="limit must be a positive integer",
            )
        fts_query = _fts_safe_query(query)
        if fts_query is None:
            return _invalid_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.EMPTY_QUERY,
                message="query must contain searchable text",
            )
        try:
            # Ask the existing FTS adapter for its maximum bounded candidate set,
            # then enforce the run-specific source scope before returning evidence.
            hits = search_source_blocks(self._database, fts_query, limit=_MAX_FTS_CANDIDATES)
            scoped_hits = [hit for hit in hits if hit.source_ref in allowed_scope][:effective_limit]
            text_hashes = _text_hashes(self._database, [hit.source_ref for hit in scoped_hits])
        except sqlite3.Error:
            return _error_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.DATABASE_ERROR,
                message="local textbook index search failed",
                available=False,
            )
        except OSError:
            return _error_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.DATABASE_ERROR,
                message="local textbook index could not be opened",
                available=False,
            )

        evidence = tuple(
            EvidenceRecord(
                evidence_id=_stable_hash(f"textbook:{hit.source_ref}:{text_hashes.get(hit.source_ref, '')}")[:24],
                kind=EvidenceKind.TEXTBOOK,
                query=query,
                text=_compact_excerpt(hit.text, self._excerpt_char_limit),
                locator=f"page:{hit.page_number};block:{hit.block_index}",
                provider=self.name,
                source_ref=hit.source_ref,
                document_id=hit.document_id,
                page_number=hit.page_number,
                block_index=hit.block_index,
                excerpt=_compact_excerpt(hit.text, self._excerpt_char_limit),
                text_hash=text_hashes.get(hit.source_ref, _stable_hash(hit.text)),
                rank=hit.rank,
            )
            for hit in scoped_hits
        )
        return SearchResult(
            skill_name=self.name,
            skill_version=self.version,
            query=query,
            status=SearchStatus.OK if evidence else SearchStatus.NO_RESULTS,
            evidence=evidence,
            filtered_count=len(hits) - len(scoped_hits),
        )


class DisabledWebResourceSearchSkill:
    """The secure default: external resource search is explicitly unavailable."""

    name = "disabled-web-resource-search"
    version = "v1"

    def search(self, query: str, *, limit: int = 3) -> SearchResult:
        del limit
        query = _normalized_query(query) or ""
        return SearchResult(
            skill_name=self.name,
            skill_version=self.version,
            query=query,
            status=SearchStatus.DISABLED,
            available=False,
            error_code=SearchErrorCode.DISABLED_BY_CONFIGURATION,
            error_message="web resource search is disabled by configuration",
        )


class ProviderBackedWebResourceSearchSkill:
    """Policy wrapper around a provider-neutral external web search adapter."""

    name = "web-resource-search"
    version = "v1"

    def __init__(
        self,
        provider: WebSearchProvider,
        *,
        allowed_domains: Iterable[object],
        timeout_seconds: float = 10.0,
        max_results: int = 3,
        excerpt_char_limit: int = 1_000,
    ) -> None:
        if max_results < 1 or max_results > 100:
            raise ValueError("max_results must be between 1 and 100")
        if excerpt_char_limit < 32:
            raise ValueError("excerpt_char_limit must be at least 32")
        self._provider = provider
        try:
            self._allowed_domains = normalize_allowed_domains(allowed_domains)
            self._configuration_error: SearchErrorCode | None = (
                None if self._allowed_domains else SearchErrorCode.EMPTY_DOMAIN_ALLOWLIST
            )
        except (TypeError, ValueError):
            self._allowed_domains = frozenset()
            self._configuration_error = SearchErrorCode.INVALID_DOMAIN
        self._timeout_seconds = timeout_seconds
        self._max_results = max_results
        self._excerpt_char_limit = excerpt_char_limit

    def search(self, query: str, *, limit: int = 3) -> SearchResult:
        query = _normalized_query(query)
        if query is None:
            return _invalid_result(
                name=self.name,
                version=self.version,
                query="",
                code=SearchErrorCode.EMPTY_QUERY,
                message="query must contain non-whitespace text",
            )
        effective_limit = _validated_limit(limit, self._max_results)
        if effective_limit is None:
            return _invalid_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.INVALID_LIMIT,
                message="limit must be a positive integer",
            )
        timeout_seconds = self._timeout_seconds
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            return _invalid_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.INVALID_TIMEOUT,
                message="timeout_seconds must be positive",
            )
        if self._configuration_error is not None:
            return _error_result(
                name=self.name,
                version=self.version,
                query=query,
                code=self._configuration_error,
                message="web resource domain allowlist is not configured",
                available=False,
            )
        try:
            candidates = self._provider.search(
                query=query,
                limit=effective_limit,
                timeout_seconds=float(timeout_seconds),
            )
        except TimeoutError:
            return _error_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.PROVIDER_TIMEOUT,
                message="web resource provider timed out",
            )
        except Exception:
            return _error_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.PROVIDER_ERROR,
                message="web resource provider failed",
            )
        if not isinstance(candidates, Sequence):
            return _error_result(
                name=self.name,
                version=self.version,
                query=query,
                code=SearchErrorCode.INVALID_PROVIDER_RESPONSE,
                message="web resource provider returned an invalid response",
            )

        evidence: list[EvidenceRecord] = []
        filtered_count = 0
        seen_urls: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, WebSearchCandidate):
                return _error_result(
                    name=self.name,
                    version=self.version,
                    query=query,
                    code=SearchErrorCode.INVALID_PROVIDER_RESPONSE,
                    message="web resource provider returned an invalid candidate",
                )
            normalized_url = normalize_https_url(candidate.url, self._allowed_domains)
            if normalized_url is None or normalized_url in seen_urls:
                filtered_count += 1
                continue
            if not isinstance(candidate.title, str) or not isinstance(candidate.excerpt, str):
                return _error_result(
                    name=self.name,
                    version=self.version,
                    query=query,
                    code=SearchErrorCode.INVALID_PROVIDER_RESPONSE,
                    message="web resource provider returned invalid candidate text",
                )
            seen_urls.add(normalized_url)
            excerpt = _compact_excerpt(candidate.excerpt, self._excerpt_char_limit)
            text_hash = candidate.content_hash or _stable_hash(excerpt)
            evidence.append(
                EvidenceRecord(
                    evidence_id=_stable_hash(f"web:{normalized_url}:{text_hash}")[:24],
                    kind=EvidenceKind.WEB,
                    query=query,
                    text=excerpt,
                    locator=normalized_url,
                    provider=getattr(self._provider, "name", "unknown"),
                    url=normalized_url,
                    title=candidate.title.strip() or normalized_url,
                    publisher=candidate.publisher.strip() if isinstance(candidate.publisher, str) and candidate.publisher.strip() else None,
                    excerpt=excerpt,
                    text_hash=text_hash,
                    rank=candidate.rank,
                )
            )
            if len(evidence) >= effective_limit:
                break
        return SearchResult(
            skill_name=self.name,
            skill_version=self.version,
            query=query,
            status=SearchStatus.OK if evidence else SearchStatus.NO_RESULTS,
            evidence=tuple(evidence),
            filtered_count=filtered_count,
            metadata={
                "provider": getattr(self._provider, "name", "unknown"),
                "provider_version": getattr(self._provider, "version", "unknown"),
            },
        )


class DeterministicMockWebSearchProvider:
    """In-memory provider for repeatable unit and workflow tests."""

    name = "deterministic-mock-web"
    version = "v1"

    def __init__(self, candidates_by_query: Mapping[str, Sequence[WebSearchCandidate]] | None = None) -> None:
        self._candidates_by_query = {
            " ".join(query.split()): tuple(candidates)
            for query, candidates in (candidates_by_query or {}).items()
        }

    def search(
        self,
        *,
        query: str,
        limit: int,
        timeout_seconds: float,
    ) -> Sequence[WebSearchCandidate]:
        del timeout_seconds
        return self._candidates_by_query.get(" ".join(query.split()), ())[:limit]


class DeterministicMockWebResourceSearchSkill(ProviderBackedWebResourceSearchSkill):
    """A full web-skill replacement with no network access or hidden state."""

    name = "deterministic-mock-web-resource-search"
    version = "v1"

    def __init__(
        self,
        candidates_by_query: Mapping[str, Sequence[WebSearchCandidate]] | None = None,
        *,
        allowed_domains: Iterable[object] = ("example.test",),
        timeout_seconds: float = 10.0,
        max_results: int = 3,
        excerpt_char_limit: int = 1_000,
    ) -> None:
        super().__init__(
            DeterministicMockWebSearchProvider(candidates_by_query),
            allowed_domains=allowed_domains,
            timeout_seconds=timeout_seconds,
            max_results=max_results,
            excerpt_char_limit=excerpt_char_limit,
        )
