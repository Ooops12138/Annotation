"""Structured local human-review records and immutable report revisions.

P0 deliberately keeps review operations outside the product UI and database.
This module provides the small JSON contract used by a reviewer or a local
script, while keeping the original ReviewReport and generated artifacts
unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from annotation.config import STORAGE_DIR
from annotation.domain.artifacts import ReviewIssue, ReviewReport


ReviewVerdict = Literal["passed", "needs_revision", "blocked"]
ReviewCategory = Literal[
    "fact",
    "logic",
    "formula",
    "coverage",
    "source",
    "transition",
    "conflict",
    "uncertainty",
    "stance",
]


class HumanReviewEntry(BaseModel):
    """One reviewer decision for a quiz, material, artifact, or IR node."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    target_type: Literal["artifact", "node", "question", "material", "document"] = "artifact"
    verdict: ReviewVerdict
    category: ReviewCategory
    message: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)
    suggested_action: str | None = None


class HumanReviewRecord(BaseModel):
    """Versioned local JSON record for a focused manual review sample."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["human-review-v1"] = "human-review-v1"
    review_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    reviewed_artifact_id: str = Field(min_length=1)
    entries: list[HumanReviewEntry] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    artifact_path: str | None = None


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "review"


def _immutable_path(path: Path) -> Path:
    if not path.exists():
        return path
    revision = 2
    while True:
        candidate = path.with_name(f"{path.stem}-revision-{revision}{path.suffix}")
        if not candidate.exists():
            return candidate
        revision += 1


def write_human_review_record(
    record: HumanReviewRecord,
    *,
    root: str | Path | None = None,
) -> Path:
    """Persist an immutable local review record and return its path."""

    directory = Path(root or (STORAGE_DIR / "artifacts" / "human-review")) / _safe_id(record.run_id)
    path = _immutable_path(directory / f"{_safe_id(record.review_id)}.json")
    record.artifact_path = str(path.resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": "human-review-artifact-v1", "record": record.model_dump(mode="json")},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _entry_issue(record: HumanReviewRecord, entry: HumanReviewEntry) -> ReviewIssue | None:
    if entry.verdict == "passed":
        return None
    severity = "blocking"
    message_prefix = "人工复核要求修订" if entry.verdict == "needs_revision" else "人工复核阻塞"
    return ReviewIssue(
        issue_id=f"human-review-{_safe_id(record.review_id)}-{_safe_id(entry.entry_id)}",
        category=entry.category,
        severity=severity,
        layer="fact" if entry.category in {"fact", "formula", "uncertainty"} else "structure",
        message=f"{message_prefix}：{entry.message}",
        target_id=entry.target_id,
        source_refs=list(dict.fromkeys(entry.source_refs)),
        suggested_action=entry.suggested_action,
    )


def mark_reviewed_artifact(artifact: Any, record: HumanReviewRecord) -> Any:
    """Return a status-adjusted copy without overwriting the original object."""

    target_id = getattr(artifact, "artifact_id", None)
    relevant = [entry for entry in record.entries if entry.target_id == target_id]
    if not relevant:
        return artifact
    status = getattr(artifact, "status", None)
    if any(entry.verdict == "blocked" for entry in relevant):
        next_status = "blocked"
    elif any(entry.verdict == "needs_revision" for entry in relevant):
        next_status = "needs_revision"
    else:
        return artifact
    if hasattr(artifact, "model_copy"):
        return artifact.model_copy(update={"status": next_status}, deep=True)
    return artifact


def merge_human_review(
    report: ReviewReport,
    record: HumanReviewRecord,
    *,
    root: str | Path | None = None,
    persist_record: bool = True,
) -> ReviewReport:
    """Create a new ReviewReport version from a manual review record.

    Existing issues are retained. A manual ``needs_revision`` decision keeps
    the new report out of publication; ``blocked`` has the stronger terminal
    status. Passed entries are represented in the checks map without erasing
    earlier automated warnings.
    """

    if record.run_id != report.run_id:
        raise ValueError("human review run_id does not match ReviewReport")
    if record.reviewed_artifact_id != report.reviewed_artifact_id:
        raise ValueError("human review target does not match ReviewReport")
    if persist_record:
        write_human_review_record(record, root=root)

    issues = list(report.issues)
    checks = dict(report.checks)
    recommendations = list(report.recommendations)
    manual_statuses: list[ReviewVerdict] = []
    for entry in record.entries:
        manual_statuses.append(entry.verdict)
        checks[f"human_review:{entry.target_id}"] = entry.verdict
        issue = _entry_issue(record, entry)
        if issue is not None and issue.issue_id not in {existing.issue_id for existing in issues}:
            issues.append(issue)
        if entry.suggested_action and entry.suggested_action not in recommendations:
            recommendations.append(entry.suggested_action)
    issue_counts = {
        severity: sum(issue.severity == severity for issue in issues)
        for severity in ("info", "warning", "blocking")
    }
    if "blocked" in manual_statuses or issue_counts["blocking"] and report.status == "blocked":
        status: Literal["passed", "at_risk", "blocked", "needs_revision"] = "blocked"
    elif "needs_revision" in manual_statuses:
        status = "needs_revision"
    elif issue_counts["blocking"]:
        status = "blocked"
    elif issue_counts["warning"] or report.status == "at_risk":
        status = "at_risk"
    else:
        status = "passed"
    next_version = report.version + 1
    merged = ReviewReport(
        artifact_id=f"review-{report.run_id}-v{next_version}",
        report_id=f"review-{report.run_id}-v{next_version}",
        run_id=report.run_id,
        version=next_version,
        status=status,
        created_by=f"reviewer:{record.reviewer}",
        document_id=report.document_id,
        reviewed_artifact_id=report.reviewed_artifact_id,
        source_refs=list(report.source_refs),
        issues=issues,
        issue_counts=issue_counts,
        checks=checks,
        human_review_required=False,
        revision_of=report.report_id,
        recommendations=recommendations,
    )
    merged.human_review_required = any(entry.verdict != "passed" for entry in record.entries)
    path = _review_report_path(merged, root=root)
    merged.artifact_path = str(path.resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": "review-report-v1", "report": merged.model_dump(mode="json")},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return merged


def _review_report_path(report: ReviewReport, *, root: str | Path | None = None) -> Path:
    directory = Path(root or (STORAGE_DIR / "artifacts" / "review")) / _safe_id(report.run_id)
    return _immutable_path(directory / f"review-v{report.version}.json")


# Short aliases keep the local script/API discoverable without duplicating
# the persistence implementation.
write_human_review = write_human_review_record
apply_human_review = merge_human_review


__all__ = [
    "HumanReviewEntry",
    "HumanReviewRecord",
    "apply_human_review",
    "mark_reviewed_artifact",
    "merge_human_review",
    "write_human_review",
    "write_human_review_record",
]
