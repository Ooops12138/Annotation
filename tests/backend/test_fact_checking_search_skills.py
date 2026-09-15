from __future__ import annotations

import sqlite3

from annotation.domain.artifacts import SourceBlock
from annotation.fact_checking import (
    DeterministicMockWebResourceSearchSkill,
    DisabledWebResourceSearchSkill,
    EvidenceKind,
    SearchErrorCode,
    SearchStatus,
    SQLiteFts5TextbookDatabaseSearchSkill,
    TextbookDatabaseSearchSkill,
    WebResourceSearchSkill,
    WebSearchCandidate,
    normalize_https_url,
)
from annotation.retrieval import index_source_blocks


def _block(source_ref: str, text: str, *, page: int, block: int) -> SourceBlock:
    return SourceBlock(
        artifact_id=f"artifact-{source_ref}",
        source_ref=source_ref,
        document_id="document-1",
        run_id="run-1",
        version=1,
        status="draft",
        source_refs=[source_ref],
        created_by="test",
        page_number=page,
        block_index=block,
        text=text,
        text_hash=f"hash-{source_ref}",
        parser_version="test-v1",
        bbox=(0, 0, 1, 1),
    )


def _indexed_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    index_source_blocks(
        connection,
        [
            _block("src-allowed", "The completeness axiom has an upper bound.", page=2, block=1),
            _block("src-denied", "The completeness axiom is explained elsewhere.", page=9, block=4),
            _block("src-other", "A different theorem is here.", page=11, block=2),
        ],
    )
    return connection


def test_textbook_skill_scopes_evidence_and_preserves_trace_fields() -> None:
    connection = _indexed_connection()
    skill = SQLiteFts5TextbookDatabaseSearchSkill(connection, max_results=2)

    result = skill.search(
        "completeness axiom",
        allowed_source_refs={"src-allowed"},
        limit=1,
    )

    assert isinstance(skill, TextbookDatabaseSearchSkill)
    assert result.status == SearchStatus.OK
    assert result.available is True
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.kind == EvidenceKind.TEXTBOOK
    assert evidence.source_ref == "src-allowed"
    assert evidence.url is None
    assert evidence.text == evidence.excerpt
    assert evidence.text_hash == "hash-src-allowed"
    assert evidence.locator == "page:2;block:1"
    assert evidence.provider == skill.name
    assert evidence.page_number == 2


def test_textbook_skill_returns_stable_request_and_database_errors() -> None:
    connection = _indexed_connection()
    skill = SQLiteFts5TextbookDatabaseSearchSkill(connection)

    empty_query = skill.search("   ", allowed_source_refs={"src-allowed"})
    empty_scope = skill.search("axiom", allowed_source_refs=set())
    invalid_limit = skill.search("axiom", allowed_source_refs={"src-allowed"}, limit=0)

    assert (empty_query.status, empty_query.error_code) == (
        SearchStatus.INVALID_REQUEST,
        SearchErrorCode.EMPTY_QUERY,
    )
    assert (empty_scope.status, empty_scope.error_code) == (
        SearchStatus.INVALID_REQUEST,
        SearchErrorCode.EMPTY_SOURCE_SCOPE,
    )
    assert (invalid_limit.status, invalid_limit.error_code) == (
        SearchStatus.INVALID_REQUEST,
        SearchErrorCode.INVALID_LIMIT,
    )

    connection.close()
    unavailable = skill.search("axiom", allowed_source_refs={"src-allowed"})
    assert (unavailable.status, unavailable.error_code, unavailable.available) == (
        SearchStatus.ERROR,
        SearchErrorCode.DATABASE_ERROR,
        False,
    )


def test_disabled_web_skill_exposes_disabled_availability_without_network() -> None:
    skill = DisabledWebResourceSearchSkill()

    result = skill.search("external fact", limit=1)

    assert isinstance(skill, WebResourceSearchSkill)
    assert result.status == SearchStatus.DISABLED
    assert result.error_code == SearchErrorCode.DISABLED_BY_CONFIGURATION
    assert result.available is False
    assert result.evidence == ()


def test_mock_web_skill_filters_urls_normalizes_https_and_is_deterministic() -> None:
    candidates = {
        "external fact": [
            WebSearchCandidate(
                url="HTTPS://Docs.Example.ORG/path#section",
                title="Approved source",
                excerpt="Approved external evidence.",
                publisher="Example Org",
                content_hash="known-content-hash",
            ),
            WebSearchCandidate(
                url="http://docs.example.org/insecure",
                title="Insecure source",
                excerpt="This result must be rejected.",
            ),
            WebSearchCandidate(
                url="https://docs.example.org.evil.test/not-approved",
                title="Wrong suffix",
                excerpt="This result must be rejected.",
            ),
        ]
    }
    skill = DeterministicMockWebResourceSearchSkill(
        candidates,
        allowed_domains={"example.org"},
    )

    first = skill.search("external fact", limit=3)
    second = skill.search("external fact", limit=3)

    assert first == second
    assert first.status == SearchStatus.OK
    assert first.available is True
    assert len(first.evidence) == 1
    evidence = first.evidence[0]
    assert evidence.kind == EvidenceKind.WEB
    assert evidence.source_ref is None
    assert evidence.url == "https://docs.example.org/path"
    assert evidence.locator == evidence.url
    assert evidence.provider == "deterministic-mock-web"
    assert first.filtered_count == 2
    assert normalize_https_url("https://example.org/a#fragment", {"example.org"}) == "https://example.org/a"
    assert normalize_https_url("https://example.org.evil.test/a", {"example.org"}) is None


def test_mock_web_skill_reports_invalid_allowlist_and_bounds_results() -> None:
    candidates = {
        "fact": [
            WebSearchCandidate("https://example.org/one", "One", "One"),
            WebSearchCandidate("https://example.org/two", "Two", "Two"),
        ]
    }
    misconfigured = DeterministicMockWebResourceSearchSkill(candidates, allowed_domains={"https://example.org"})
    invalid = misconfigured.search("fact")

    bounded = DeterministicMockWebResourceSearchSkill(
        candidates,
        allowed_domains={"example.org"},
        max_results=1,
    ).search("fact", limit=99)

    assert (invalid.status, invalid.error_code, invalid.available) == (
        SearchStatus.ERROR,
        SearchErrorCode.INVALID_DOMAIN,
        False,
    )
    assert bounded.status == SearchStatus.OK
    assert len(bounded.evidence) == 1
