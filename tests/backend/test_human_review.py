from pathlib import Path

import pytest

from annotation.domain.artifacts import ReviewIssue, ReviewReport
from annotation.workflow.human_review import (
    HumanReviewEntry,
    HumanReviewRecord,
    mark_reviewed_artifact,
    merge_human_review,
    write_human_review_record,
)


def _report() -> ReviewReport:
    return ReviewReport(
        artifact_id="review-run-human-v1",
        report_id="review-run-human-v1",
        run_id="run-human",
        version=1,
        status="at_risk",
        created_by="review:deterministic-v1",
        document_id="doc-human",
        reviewed_artifact_id="doc-artifact-human",
        source_refs=["src-1"],
        issues=[ReviewIssue(
            issue_id="source-warning",
            category="source",
            severity="warning",
            message="来源待确认",
            target_id="doc-artifact-human",
        )],
        issue_counts={"info": 0, "warning": 1, "blocking": 0},
        checks={"source_artifact_status": "warning"},
    )


def _record() -> HumanReviewRecord:
    return HumanReviewRecord(
        review_id="human-sample-001",
        run_id="run-human",
        reviewer="数学评审",
        reviewed_artifact_id="doc-artifact-human",
        entries=[
            HumanReviewEntry(
                entry_id="quiz-q1",
                target_id="quiz-ku-1-q1",
                target_type="question",
                verdict="passed",
                category="logic",
                message="答案唯一且解析与来源一致",
                source_refs=["src-1"],
            ),
            HumanReviewEntry(
                entry_id="material-supremum",
                target_id="content-ku-supremum",
                target_type="material",
                verdict="needs_revision",
                category="formula",
                message="逼近步骤需要逐行核对",
                source_refs=["src-1"],
                suggested_action="按教材原文重生成该材料。",
            ),
        ],
    )


def test_structured_review_record_is_immutable_json(tmp_path: Path) -> None:
    record = _record()
    path = write_human_review_record(record, root=tmp_path)
    assert path.is_file()
    assert record.artifact_path == str(path.resolve())
    assert '"human-review-v1"' in path.read_text(encoding="utf-8")
    second = write_human_review_record(record, root=tmp_path)
    assert second != path
    assert path.is_file()


def test_merge_creates_new_report_without_overwriting_old(tmp_path: Path) -> None:
    report = _report()
    merged = merge_human_review(report, _record(), root=tmp_path)
    assert report.version == 1
    assert report.status == "at_risk"
    assert merged.version == 2
    assert merged.revision_of == report.report_id
    assert merged.status == "needs_revision"
    assert merged.human_review_required is True
    assert any(issue.category == "formula" and issue.severity == "blocking" for issue in merged.issues)
    assert Path(merged.artifact_path).is_file()
    assert merged.checks["human_review:quiz-ku-1-q1"] == "passed"


def test_mark_reviewed_artifact_keeps_original_status() -> None:
    class Artifact:
        artifact_id = "content-ku-supremum"
        status = "accepted"

        def model_copy(self, *, update, deep):
            clone = Artifact()
            clone.status = update["status"]
            return clone

    marked = mark_reviewed_artifact(Artifact(), _record())
    assert marked.status == "needs_revision"


def test_merge_rejects_different_run_or_target() -> None:
    record = _record()
    with pytest.raises(ValueError):
        merge_human_review(_report().model_copy(update={"run_id": "other"}), record, persist_record=False)
    with pytest.raises(ValueError):
        merge_human_review(_report().model_copy(update={"reviewed_artifact_id": "other"}), record, persist_record=False)
