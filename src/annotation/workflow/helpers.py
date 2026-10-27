"""Shared blueprint conversion, document assembly and review helpers."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from annotation.config import STORAGE_DIR, first_book_pdf
from annotation.domain.artifacts import (
    CalloutNode,
    ContentArtifact,
    DocumentSection,
    InteractiveComponentArtifact,
    InteractiveComponentNode,
    KnowledgeUnit,
    bind_learning_objectives,
    LearningBlueprint,
    LearningDocument,
    MarkdownNode,
    QuizArtifact,
    QuizNode,
    ReviewIssue,
    ReviewReport,
    SourceBlock,
)
from annotation.providers import ModelProvider, ProviderError
from annotation.workflow.models import BlueprintDraft, CalloutDraft, WorkflowState

from annotation.workflow.content_support import _formula_format_issues, _lossless_refs


def default_pdf() -> Path:
    return first_book_pdf()


def source_context(blocks: list[SourceBlock], limit: int | None = None) -> str:
    """Render non-empty textbook blocks for the blueprint model input.

    The single-window path is budget checked before a model request. Long books
    use source slices in blueprint_hierarchy instead. An explicit block limit is
    retained for fixtures/experiments, never as an automatic truncation policy.
    """
    non_empty = [block for block in blocks if block.text.strip()]
    selected = non_empty if limit is None else non_empty[:limit]
    return "\n".join(
        f"[{block.source_ref}] {block.text}"
        for block in selected
    )


def response_metadata(response: Any) -> dict[str, Any]:
    return {"provider": response.provider, "model": response.model, "base_url": response.base_url, "config_version": response.config_version, "duration_ms": response.duration_ms, "usage": dict(response.usage)}


def write_review_report(report: ReviewReport) -> Path:
    """Persist each review result as a separate immutable-by-convention artifact."""

    root = STORAGE_DIR / "artifacts" / "review" / report.run_id
    root.mkdir(parents=True, exist_ok=True)
    # Re-running a workflow with the same run id is a new review version.  A
    # previous report is evidence and must remain readable for comparison.
    version = report.version
    while (root / f"review-v{version}.json").exists():
        version += 1
    if version != report.version:
        report.version = version
        report.artifact_id = f"review-{report.run_id}-v{version}"
        report.report_id = f"review-{report.run_id}-v{version}"
        report.revision_of = f"review-{report.run_id}-v{version - 1}"
    path = root / f"review-v{version}.json"
    report.artifact_path = str(path.resolve())
    path.write_text(
        json.dumps(
            {"schema_version": "review-report-v1", "report": report.model_dump(mode="json")},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def review_report_for(document: LearningDocument, state: WorkflowState) -> ReviewReport:
    """Create the small, deterministic T-008 review report.

    The report deliberately annotates risk instead of claiming that an
    automated check has proved the content correct.  The existing document
    issues remain attached to the document and are copied into this versioned
    review artifact for auditability.
    """

    issues = list(document.issues)
    valid_refs = set(state.get("source_refs", []))
    source_document = state.get("source_document")
    checks: dict[str, str] = {
        "document_structure": "passed" if document.sections and any(section.children for section in document.sections) else "blocking",
        "source_traceability": "passed" if document.source_refs and set(document.source_refs).issubset(valid_refs) else "blocking",
    }
    # Parsed source artifacts are intentionally draft until a human confirms
    # the extraction.  Keep that distinction visible in the review artifact;
    # it must not be silently treated as an approved textbook source.
    source_status = getattr(source_document, "status", None)
    if source_status == "draft":
        checks["source_artifact_status"] = "warning"
        issues.append(ReviewIssue(
            issue_id="review-source-artifact-draft",
            category="source",
            severity="warning",
            layer="fact",
            message="教材来源 artifact 仍处于 draft，尚未完成人工确认；当前内容仅可作为待核实预览。",
            target_id=getattr(source_document, "artifact_id", None),
            source_refs=list(getattr(source_document, "source_refs", []) or []),
            suggested_action="抽样核对页码、文本和公式候选后，再将来源标记为已确认。",
        ))
    elif source_status in {"accepted", "published"}:
        checks["source_artifact_status"] = "passed"
    else:
        checks["source_artifact_status"] = "warning"
    nodes = [node for section in document.sections for node in section.children]
    quiz_nodes = [node for node in nodes if getattr(node, "type", None) == "quiz"]
    quiz_coverage = state.get("quiz_coverage_report")
    coverage_status = str(getattr(quiz_coverage, "status", "")) if quiz_coverage is not None else ""
    # An empty quiz set is a valid agent decision under the adaptive policy.
    # Coverage failures remain diagnostics for optional quizzes.
    checks["learning_activity"] = "passed" if quiz_nodes else "optional"
    if quiz_coverage is not None:
        checks["quiz_coverage"] = "passed" if coverage_status == "passed" else "warning"
        checks["quiz_question_count"] = "passed" if coverage_status == "passed" else "warning"
    malformed_quizzes = [
        getattr(node, "id", "unknown")
        for node in quiz_nodes
        if (
            not str(getattr(node, "question", "") or "").strip()
            or len(getattr(node, "options", []) or []) < 2
            or len(set(getattr(node, "options", []) or [])) != len(getattr(node, "options", []) or [])
            or getattr(node, "answer", None) not in (getattr(node, "options", []) or [])
            or (getattr(node, "options", []) or []).count(getattr(node, "answer", None)) != 1
            or not str(getattr(node, "explanation", "") or "").strip()
        )
    ]
    if malformed_quizzes:
        checks["quiz_integrity"] = "blocking"
        issues.append(ReviewIssue(
            issue_id="review-invalid-quiz",
            category="logic",
            severity="blocking",
            layer="structure",
            message=f"测验节点的题干、选项、答案或解析不完整：{', '.join(malformed_quizzes)}。",
            target_id=document.document_id,
            suggested_action="确认答案属于选项，并补齐至少两个可选项。",
        ))
    else:
        checks["quiz_integrity"] = "passed"
    formula_format_issues = _formula_format_issues(nodes)
    checks["formula_format"] = "blocking" if formula_format_issues else "passed"
    if formula_format_issues:
        checks["formula_integrity"] = "blocking"
        issues.append(ReviewIssue(
            issue_id="review-invalid-formula-format",
            category="formula",
            severity="blocking",
            layer="structure",
            message="；".join(formula_format_issues),
            target_id=document.document_id,
            suggested_action="使用成对的 Markdown 数学定界符，并将公式直接写在 Markdown 正文或提示框内容中。",
        ))
    else:
        checks["formula_integrity"] = "passed"
    invalid_node_refs = []
    for node in nodes:
        node_refs = list(getattr(node, "source_refs", []) or [])
        if node_refs and not set(node_refs).issubset(valid_refs):
            invalid_node_refs.append(getattr(node, "id", "unknown"))
    if invalid_node_refs:
        checks["node_source_traceability"] = "blocking"
        issues.append(ReviewIssue(
            issue_id="review-invalid-node-sources",
            category="source",
            severity="blocking",
            layer="structure",
            message=f"有节点包含无法回溯的教材引用：{', '.join(invalid_node_refs)}。",
            target_id=document.document_id,
            suggested_action="修订节点来源引用，或将其标记为补充资料并提供独立来源。",
        ))
    else:
        checks["node_source_traceability"] = "passed"

    # Extraction and uncertainty warnings are fact-risk annotations.  They
    # should be shown neutrally to a learner and routed to human review rather
    # than converted into a false yes/no correctness verdict.
    fact_risk = any(issue.layer == "fact" or issue.category in {"fact", "uncertainty"} for issue in issues)
    human_review_required = any(
        issue.severity == "blocking"
        or issue.layer in {"fact", "stance"}
        or issue.category in {"fact", "formula", "uncertainty", "conflict"}
        for issue in issues
    )
    if fact_risk:
        checks["fact_risk_annotation"] = "warning"
    else:
        checks["fact_risk_annotation"] = "passed"

    issue_counts = {severity: sum(issue.severity == severity for issue in issues) for severity in ("info", "warning", "blocking")}
    if issue_counts["blocking"]:
        review_status = "blocked"
    elif issue_counts["warning"] or human_review_required:
        review_status = "at_risk"
    else:
        review_status = "passed"
    report = ReviewReport(
        artifact_id=f"review-{document.run_id}-v{document.version}",
        report_id=f"review-{document.run_id}-v{document.version}",
        run_id=document.run_id,
        version=document.version,
        status=review_status,
        created_by="review:deterministic-v1",
        document_id=document.document_id,
        reviewed_artifact_id=document.artifact_id,
        source_refs=list(document.source_refs),
        issues=issues,
        issue_counts=issue_counts,
        checks=checks,
        human_review_required=human_review_required,
        recommendations=(
            ["逐条核对事实风险和教材公式；审核结论仅表示值得进一步核查。"]
            if fact_risk else []
        ),
    )
    path = write_review_report(report)
    report.artifact_path = str(path.resolve())
    return report


def assemble_document_from_artifacts(
    run_id: str,
    blueprint: LearningBlueprint,
    artifacts: list[ContentArtifact],
    provider_name: str,
    quiz_artifacts: list[QuizArtifact] | None = None,
    interactive_component_artifacts: list[InteractiveComponentArtifact] | None = None,
) -> LearningDocument:
    by_unit: dict[str, list[ContentArtifact]] = {}
    for artifact in artifacts:
        for unit_id in artifact.knowledge_unit_ids:
            by_unit.setdefault(unit_id, []).append(artifact)
    quiz_by_unit: dict[str, list[QuizArtifact]] = {}
    for artifact in quiz_artifacts or []:
        for unit_id in artifact.knowledge_unit_ids:
            quiz_by_unit.setdefault(unit_id, []).append(artifact)
    component_by_unit: dict[str, list[InteractiveComponentArtifact]] = {}
    for artifact in interactive_component_artifacts or []:
        component_by_unit.setdefault(artifact.knowledge_unit_id, []).append(artifact)
    sections: list[DocumentSection] = []
    for index, unit in enumerate(blueprint.knowledge_units, start=1):
        unit_artifacts = by_unit.get(unit.artifact_id, [])
        children: list[Any] = []
        for artifact in unit_artifacts:
            children.append(MarkdownNode(
                id=f"{artifact.artifact_id}-markdown",
                content=artifact.content,
                source_refs=list(artifact.source_refs),
            ))
            raw_callouts = artifact.metadata.get("callouts", [])
            if not isinstance(raw_callouts, list):
                raise ValueError(f"invalid_callouts:{artifact.artifact_id}:expected_list")
            for callout_index, raw_callout in enumerate(raw_callouts, start=1):
                try:
                    callout = CalloutDraft.model_validate(raw_callout)
                except Exception as exc:
                    raise ValueError(f"invalid_callout:{artifact.artifact_id}:{callout_index}:{exc}") from exc
                children.append(CalloutNode(
                    id=f"{artifact.artifact_id}-callout-{callout_index}",
                    tone=callout.tone,
                    title=callout.title,
                    content=callout.content,
                    source_refs=list(artifact.source_refs),
                ))
        # A-004 specs are accepted before assembly and remain immutable in the
        # learner IR. They deliberately sit after the explanation and before
        # independently generated quiz questions for the same unit.
        for component_artifact in component_by_unit.get(unit.artifact_id, []):
            children.append(InteractiveComponentNode(
                id=f"{component_artifact.artifact_id}-node",
                artifact_id=component_artifact.artifact_id,
                spec=component_artifact.spec,
                source_refs=list(component_artifact.source_refs),
            ))
        # Quiz artifacts are independently checked before assembly.  Keep the
        # learner-facing projection deliberately small and preserve the exact
        # item/source identity in the artifact rather than adding hidden fields
        # to the renderer contract.
        for quiz_artifact in quiz_by_unit.get(unit.artifact_id, []):
            for question in quiz_artifact.questions:
                children.append(QuizNode(
                    id=question.question_id,
                    question=question.question,
                    options=list(question.options),
                    answer=question.answer,
                    explanation=question.explanation,
                    source_refs=list(question.source_refs),
                ))
        sections.append(DocumentSection(id=f"section-{index:03d}", title=unit.title, children=children))
    refs: list[str] = []
    for artifact in artifacts:
        for ref in artifact.source_refs:
            if ref not in refs:
                refs.append(ref)
    for artifact in quiz_artifacts or []:
        for ref in artifact.source_refs:
            if ref not in refs:
                refs.append(ref)
    for artifact in interactive_component_artifacts or []:
        for ref in artifact.source_refs:
            if ref not in refs:
                refs.append(ref)
    return LearningDocument(
        artifact_id=f"doc-{uuid.uuid4().hex[:12]}",
        document_id=f"doc-{run_id}",
        blueprint_version=f"{blueprint.artifact_id}:v{blueprint.version}",
        run_id=run_id,
        version=1,
        status="draft",
        source_refs=refs[:5],
        created_by=f"provider:{provider_name}",
        title=blueprint.title,
        sections=sections,
    )


def blueprint_from_draft(
    *,
    draft: BlueprintDraft,
    state: WorkflowState,
    provider_name: str,
    workflow_capabilities: Any | None = None,
) -> tuple[LearningBlueprint, bool]:
    """Convert a validated model draft without provider-profile heuristics."""

    # Re-validate here even when a test provider hands us a model instance.
    # This keeps the production boundary strict for enum values such as kind.
    draft = BlueprintDraft.model_validate(draft.model_dump(mode="python"))
    ordered_refs = list(state["source_refs"])
    units: list[KnowledgeUnit] = []
    adapted = False
    used_unit_ids: set[str] = set()
    previous = state.get("blueprint")
    previous_units = {u.artifact_id: u for u in previous.knowledge_units} if previous else {}
    reserved_ids = set(previous.objective_id_history) if previous else set()
    reserved_ids.update(o.objective_id for u in previous_units.values() for o in u.learning_objectives)
    for index, unit in enumerate(draft.knowledge_units, start=1):
        if workflow_capabilities is not None:
            refs, refs_adapted = workflow_capabilities.blueprint_source_refs(unit, state)
            adapted = adapted or refs_adapted
        else:
            refs = _lossless_refs(unit.source_refs)
        requested_id = (unit.knowledge_unit_id or "").strip()
        stable_id = requested_id or f"ku-{hashlib.sha256(unit.title.strip().encode('utf-8')).hexdigest()[:12]}"
        if stable_id in used_unit_ids:
            raise ProviderError(f"duplicate knowledge unit id: {stable_id}", category="schema")
        used_unit_ids.add(stable_id)
        known_ids = set(previous_units[stable_id].objective_ids) if stable_id in previous_units else set()
        for objective in unit.learning_objectives:
            if not isinstance(objective, str) and objective.objective_id not in known_ids:
                raise ProviderError(f"unknown retained objective_id: {objective.objective_id}", category="schema")
        objectives = bind_learning_objectives(stable_id, list(unit.learning_objectives), reserved_ids)
        reserved_ids.update(o.objective_id for o in objectives)
        units.append(KnowledgeUnit(
            artifact_id=stable_id,
            run_id=state["run_id"],
            version=1,
            status="draft",
            source_refs=refs,
            created_by=f"provider:{provider_name}",
            title=unit.title,
            kind=unit.kind,
            learning_objectives=objectives,
            prerequisites=list(unit.prerequisites),
            related_unit_ids=list(unit.related_unit_ids),
            structure_node_ids=[state["textbook_structure"].nodes[0].node_id] if state.get("textbook_structure") else [],
        ))
    if not units:
        raise ProviderError("blueprint contains no knowledge units", category="schema")
    artifact_id = (workflow_capabilities.blueprint_artifact_id(f"bp-{uuid.uuid4().hex[:12]}")
                   if workflow_capabilities is not None else f"bp-{uuid.uuid4().hex[:12]}")
    blueprint = LearningBlueprint(
        artifact_id=artifact_id,
        run_id=state["run_id"],
        version=1,
        status="checking",
        source_refs=ordered_refs,
        created_by=f"provider:{provider_name}",
        title=draft.title or state["source_document"].title,
        knowledge_units=units,
        objective_id_history=sorted(reserved_ids),
    )
    return blueprint, adapted


def provider_metadata(provider: ModelProvider) -> dict[str, Any]:
    return {
        "provider": getattr(provider, "provider", "unknown"),
        "model": getattr(provider, "model", "unknown"),
        "base_url": getattr(provider, "base_url", None),
        "config_version": getattr(provider, "config_version", "unknown"),
    }
