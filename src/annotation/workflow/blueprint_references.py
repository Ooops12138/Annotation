"""One deterministic reference rule for planning and final validation."""

import unicodedata
from collections import defaultdict
from collections.abc import Iterable

from annotation.domain.artifacts import KnowledgeUnit


def normalized_title(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def title_index(units: Iterable[KnowledgeUnit]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for unit in units:
        index[normalized_title(unit.title)].add(unit.artifact_id)
    return dict(index)


def resolve_reference(ref: str, unit_ids: set[str], titles: dict[str, set[str]]) -> str | None:
    if ref in unit_ids:
        return ref
    matches = titles.get(normalized_title(ref), set())
    if len(matches) == 1:
        return next(iter(matches))
    return None


def normalize_relationships(units: dict[str, KnowledgeUnit]) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    titles = title_index(units.values())
    ids = set(units)
    for unit in units.values():
        for field in ("prerequisites", "related_unit_ids"):
            refs = []
            for ref in getattr(unit, field):
                resolved = resolve_reference(ref, ids, titles) or ref
                refs.append(resolved)
                if resolved != ref:
                    changes.append({"unit_id": unit.artifact_id, "field": field, "original": ref, "resolved": resolved})
            setattr(unit, field, list(dict.fromkeys(refs)))
    return changes
