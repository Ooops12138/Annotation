"""Import an already-completed model run into the local document library.

This command is intentionally offline: it reads the existing source/content
artifacts and model-call log, then reconstructs the Document IR and indexes it
in SQLite.  It never creates a provider or sends a model request.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from annotation.api.persistence_service import load_run_payload, persist_workflow_result, project_document_status, repository
from annotation.config import STORAGE_DIR, first_book_pdf
from annotation.domain.artifacts import (
    BlueprintCheckResult,
    ContentArtifact,
    ContentTask,
    LearningBlueprint,
    LearningDocument,
    KnowledgeUnit,
    ReviewReport,
    SourceBlock,
    SourceDocument,
)
from annotation.workflow.graph import _assemble_document_from_artifacts
from annotation.workflow.persistence import persist_workflow_snapshots
from annotation.workflow.blueprint_checks import validate_blueprint


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _blueprint_call(run_id: str, calls_path: Path) -> dict[str, Any]:
    found: dict[str, Any] | None = None
    for line in calls_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        metadata = record.get("metadata") or {}
        if metadata.get("run_id") == run_id and metadata.get("agent") == "load_or_create_blueprint" and record.get("status", "success") == "success":
            found = record.get("parsed_output") or {}
    if not found:
        raise RuntimeError(f"No successful blueprint call found for {run_id}")
    return found


def _legacy_content_task(value: Any) -> dict[str, Any]:
    """Project a historic task into the active two-path task contract."""

    payload = dict(value) if isinstance(value, dict) else {}
    payload["content_types"] = ["explanation", "quiz"]
    return payload


def _legacy_content_artifact(value: Any) -> dict[str, Any]:
    """Keep historic prose readable without re-emitting retired artifact roles."""

    payload = dict(value) if isinstance(value, dict) else {}
    payload.pop("material_role", None)
    payload.pop("parent_artifact_id", None)
    payload["content_type"] = "explanation"
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        payload["metadata"] = {
            key: item
            for key, item in metadata.items()
            if key not in {
                "teaching_materials",
                "candidate_teaching_material",
                "material_kind",
                "material_completeness",
                "human_review_note",
            }
        }
    return payload


def reconstruct_state(run_id: str, root: Path) -> dict[str, Any]:
    source_payload = _load(root / "artifacts" / "ingestion" / run_id / "source.json")
    content_payload = _load(root / "artifacts" / "content" / run_id / "content.json")
    review_payload = _load(root / "artifacts" / "review" / run_id / "review-v1.json")
    source_document = SourceDocument.model_validate(source_payload["document"])
    source_blocks = [SourceBlock.model_validate(item) for item in source_payload.get("blocks", [])]
    tasks = [
        ContentTask.model_validate(_legacy_content_task(item))
        for item in content_payload.get("tasks", [])
    ]
    content_artifacts = [
        ContentArtifact.model_validate(_legacy_content_artifact(item))
        for item in content_payload.get("content_artifacts", [])
    ]
    report = ReviewReport.model_validate(review_payload.get("report", review_payload))
    draft = _blueprint_call(run_id, root / "model-calls.jsonl")

    task_by_unit = {task.knowledge_unit_id: task for task in tasks}
    valid_refs = set(source_document.source_refs)
    units: list[KnowledgeUnit] = []
    for index, raw_unit in enumerate(draft.get("knowledge_units", []), start=1):
        task = tasks[index - 1] if index <= len(tasks) else None
        unit_id = task.knowledge_unit_id if task else f"ku-{run_id}-legacy-{index:03d}"
        raw_refs = raw_unit.get("source_refs") or (task.source_refs if task else [])
        refs = [str(ref) for ref in raw_refs if str(ref) in valid_refs]
        if task:
            refs = refs or [ref for ref in task.source_refs if ref in valid_refs]
        kind = raw_unit.get("kind", "concept")
        if kind not in {"concept", "formula", "theorem", "example", "skill"}:
            kind = "concept"
        units.append(KnowledgeUnit(
            artifact_id=unit_id,
            run_id=run_id,
            version=1,
            status="accepted",
            source_refs=refs,
            created_by="legacy-import:model-call-log",
            title=str(raw_unit.get("title") or unit_id),
            kind=kind,
            learning_objectives=[str(value) for value in raw_unit.get("learning_objectives", [])],
            prerequisites=[str(value) for value in raw_unit.get("prerequisites", [])],
        ))
    if not units:
        # Content artifacts still provide enough identity to keep the old run
        # readable, but this is recorded as a review risk below.
        for index, task in enumerate(tasks, start=1):
            first = next((item for item in content_artifacts if item.task_id == task.task_id), None)
            units.append(KnowledgeUnit(
                artifact_id=task.knowledge_unit_id,
                run_id=run_id,
                version=1,
                status="accepted",
                source_refs=[ref for ref in task.source_refs if ref in valid_refs],
                created_by="legacy-import:content-artifact",
                title=first.title if first and first.title else f"知识单元 {index}",
                kind="concept",
                learning_objectives=(first.metadata.get("learning_objectives", []) if first else []),
            ))

    blueprint_version = str(content_payload.get("blueprint_version") or "bp-legacy:v1")
    blueprint_id = blueprint_version.split(":", 1)[0]
    blueprint = LearningBlueprint(
        artifact_id=blueprint_id,
        run_id=run_id,
        version=1,
        status="accepted",
        source_refs=list(source_document.source_refs),
        created_by="legacy-import:model-call-log",
        title=str(draft.get("title") or source_document.title),
        knowledge_units=units,
    )
    blueprint_check = validate_blueprint(blueprint, valid_source_refs=valid_refs)
    blueprint.issues = list(blueprint_check.issues)
    if blueprint_check.status != "accepted":
        # Keep the imported artifact readable for audit, while retaining the
        # quality-gate result instead of silently upgrading it.
        blueprint.status = "needs_revision"
    document = _assemble_document_from_artifacts(run_id, blueprint, content_artifacts, "deepseek")
    document.artifact_id = report.reviewed_artifact_id
    document.document_id = report.document_id
    # A legacy report may be warning-only.  Preserve it as a readable
    # accepted preview; only an explicitly passed review authorizes published.
    document.status = project_document_status(document.status, report.status)
    document.source_refs = list(report.source_refs or document.source_refs)
    document.title = blueprint.title
    document.created_by = "legacy-import:deepseek"
    document.issues = list(report.issues)
    document.review_report_id = report.report_id
    return {
        "run_id": run_id,
        "pdf_path": str(first_book_pdf()),
        "source_document": source_document,
        "source_blocks": source_blocks,
        "source_refs": list(source_document.source_refs),
        "blueprint": blueprint,
        "blueprint_check": blueprint_check,
        "content_tasks": tasks,
        "context_packs": [],
        "content_artifacts": content_artifacts,
        "content_artifact_checks": content_payload.get("checks", {}),
        "content_artifact_path": str((root / "artifacts" / "content" / run_id / "content.json").resolve()),
        "document": document,
        "review_report": report,
        "review_report_path": str((root / "artifacts" / "review" / run_id / "review-v1.json").resolve()),
        "provider_metadata": {"provider": "deepseek", "model": "deepseek-v4-flash", "config_version": "legacy-import"},
        "errors": [],
        "warnings": ["这是从既有 DeepSeek 运行离线重建的文档；未重新调用模型。"],
    }


def import_run(run_id: str, root: Path = STORAGE_DIR) -> dict[str, Any]:
    state = reconstruct_state(run_id, root)
    repo = repository()
    try:
        existing = repo.get_run(run_id)
        if existing and existing.get("status") == "succeeded":
            loaded = load_run_payload(repo, run_id) or {}
            return {
                "run": existing,
                "document": loaded.get("document_record"),
                "document_version": loaded.get("document_version"),
                "artifacts": {item.get("kind"): item for item in loaded.get("artifacts", [])},
            }
        persist_workflow_snapshots(state)
        book = repo.register_book(first_book_pdf())
        repo.create_run(
            book["book_id"],
            run_id=run_id,
            provider="deepseek",
            model="deepseek-v4-flash",
            config_version="legacy-import",
            origin="legacy_reconstructed",
            status="running",
        )
        result = persist_workflow_result(repo, book=book, state=state, origin="legacy_reconstructed")
        return result
    finally:
        repo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--storage", type=Path, default=STORAGE_DIR)
    args = parser.parse_args()
    result = import_run(args.run_id, args.storage)
    print(json.dumps({"run": result["run"], "document": result["document"], "document_version": result["document_version"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
