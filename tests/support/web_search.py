"""In-memory web search fixtures for fact-checking tests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from annotation.fact_checking.contracts import WebSearchCandidate
from annotation.fact_checking.skills import ProviderBackedWebResourceSearchSkill


class FixtureWebSearchProvider:
    name = "fixture-web-search"
    version = "v1"

    def __init__(self, candidates_by_query: Mapping[str, Sequence[WebSearchCandidate]] | None = None) -> None:
        self._candidates_by_query = {
            " ".join(query.split()): tuple(candidates)
            for query, candidates in (candidates_by_query or {}).items()
        }

    def search(self, *, query: str, limit: int, timeout_seconds: float) -> Sequence[WebSearchCandidate]:
        del timeout_seconds
        return self._candidates_by_query.get(" ".join(query.split()), ())[:limit]


class FixtureWebResourceSearchSkill(ProviderBackedWebResourceSearchSkill):
    name = "fixture-web-resource-search"
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
            FixtureWebSearchProvider(candidates_by_query),
            allowed_domains=allowed_domains,
            timeout_seconds=timeout_seconds,
            max_results=max_results,
            excerpt_char_limit=excerpt_char_limit,
        )
