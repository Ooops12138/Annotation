from __future__ import annotations

from annotation.api.persistence_service import _effective_document_status


def test_only_passed_review_authorizes_publication() -> None:
    assert _effective_document_status("accepted", "passed") == "published"
    assert _effective_document_status("published", "at_risk") == "accepted"
    assert _effective_document_status("published", None) == "accepted"


def test_blocked_review_wins_over_stale_document_status() -> None:
    assert _effective_document_status("published", "blocked") == "blocked"
    assert _effective_document_status("blocked", "passed") == "blocked"


def test_revision_status_is_not_promoted_to_published() -> None:
    assert _effective_document_status("accepted", "needs_revision") == "needs_revision"
