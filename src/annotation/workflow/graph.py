"""Shared workflow helpers and backward-compatible graph entry points."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from annotation.config import STORAGE_DIR, first_book_pdf
from annotation.domain.artifacts import (
    BlueprintCheckResult,
    CalloutNode,
    ContentArtifact,
    ContentCritiqueDraft,
    ContentHardCheckResult,
    ContentTask,
    ContentUnitLoopTrace,
    ContextPack,
    DocumentSection,
    KnowledgeUnit,
    LearningBlueprint,
    LearningDocument,
    MarkdownNode,
    QuizArtifact,
    QuizNode,
    ReviewIssue,
    ReviewReport,
    SourceBlock,
    SourceDocument,
)
from annotation.fixtures.demo import demo_document
from annotation.ingestion.pdf_parser import extraction_warnings, parse_pdf
from annotation.providers import ModelProvider, ProviderError
from annotation.workflow.content import select_source_refs
from annotation.workflow.models import (
    BlueprintDraft,
    BlueprintDraftUnit,
    CalloutDraft,
    ContentDraft,
    ContentReflectionState,
    DocumentDraft,
    WorkflowState,
)

from annotation.workflow.content_support import (
    _blocked_content_artifact,
    _content_artifact_from_draft,
    _content_hard_check,
    _content_issue,
    _content_loop_summary,
    _critic_review_issues,
    _dedupe_review_issues,
    _formula_format_issues,
    _lossless_refs,
    _merge_messages,
    _mock_content_critique,
    _mock_content_draft,
    _normalize_refs,
    _plan_content_tasks,
    _unescaped_token_count,
    _unit_context,
)

def _default_pdf() -> Path:
    return first_book_pdf()


def _source_context(blocks: list[SourceBlock], limit: int | None = None) -> str:
    """Render non-empty textbook blocks for the blueprint model input.

    The blueprint stage receives the complete parsed chapter by default.  A
    caller may still provide a limit for targeted fixtures or experiments.
    """
    non_empty = [block for block in blocks if block.text.strip()]
    selected = non_empty if limit is None else non_empty[:limit]
    return "\n".join(
        f"[{block.source_ref}] {re.sub(r'\\s+', ' ', block.text).strip()[:500]}"
        for block in selected
    )


def _fallback_blueprint(run_id: str, source_document: SourceDocument, blocks: list[SourceBlock]) -> LearningBlueprint:
    refs = [block.source_ref for block in blocks[:8]]
    units = []
    for index, (title, kind) in enumerate(
        [("章节核心概念", "concept"), ("定义与基本性质", "formula"), ("典型例题与方法", "example")], start=1
    ):
        units.append(KnowledgeUnit(
            artifact_id=f"ku-fallback-{index}", run_id=run_id, version=1, status="draft",
            source_refs=refs[index - 1:index + 1] or refs, created_by="fallback", title=title,
            kind=kind, learning_objectives=[f"理解{title}并能结合教材来源进行复述"],
        ))
    return LearningBlueprint(
        artifact_id=f"bp-{uuid.uuid4().hex[:12]}", run_id=run_id, version=1, status="accepted",
        source_refs=refs, created_by="fallback", title=source_document.title or "教材章节", knowledge_units=units,
    )


def fixture_blueprint(run_id: str = "run-demo-001") -> LearningBlueprint:
    """Backward-compatible fixture helper used by early contract tests."""
    document = SourceDocument(
        artifact_id="srcdoc-fixture",
        run_id=run_id,
        version=1,
        status="fixture",
        source_refs=["src-fixture-p1-b1", "src-fixture-p1-b2"],
        created_by="fixture",
        title="Fixture 教材",
        locator="fixture://demo",
    )
    blocks = [
        SourceBlock(
            artifact_id="src-fixture-p1-b1-artifact",
            source_ref="src-fixture-p1-b1",
            document_id=document.artifact_id,
            run_id=run_id,
            version=1,
            status="fixture",
            source_refs=["src-fixture-p1-b1"],
            created_by="fixture",
            page_number=1,
            block_index=1,
            text="Fixture 教材片段",
            text_hash="fixture",
            parser_version="fixture",
            bbox=(0, 0, 1, 1),
        )
    ]
    return _fallback_blueprint(run_id, document, blocks)


def _fallback_document(run_id: str, blueprint: LearningBlueprint, source_document: SourceDocument, blocks: list[SourceBlock], provider_name: str) -> LearningDocument:
    # Keep provenance on the artifact, but do not copy an entire chapter's
    # source index into every rendered node.  The UI presents these refs to a
    # learner, so a short deterministic sample is the appropriate fallback.
    refs = (blueprint.source_refs or [block.source_ref for block in blocks])[:5]
    excerpt = " ".join(re.sub(r"\s+", " ", block.text).strip() for block in blocks[:3])[:1200]
    section = DocumentSection(id="section-main", title=blueprint.title, children=[
        MarkdownNode(id="explanation-main", content=f"本节围绕“{blueprint.title}”组织学习。教材摘录：{excerpt or '教材文本未能提取，当前内容需要人工审核。'}", source_refs=refs),
        MarkdownNode(id="formula-main", content="$$\\lim_{x \\to a} f(x)=L$$", source_refs=refs),
        CalloutNode(id="review-note", tone="warning", title="来源与审核提示", content="这是基于教材片段生成的 POC 内容，发布前仍需人工核对定义、公式和例题。"),
        QuizNode(id="quiz-main", question="本节学习内容的首要事实来源是什么？", options=[source_document.title, "未提供来源", "与教材无关的外部资料"], answer=source_document.title, explanation="本 Demo 将教材 PDF 作为主来源，并保留 SourceBlock 引用。", source_refs=refs),
    ])
    return LearningDocument(
        artifact_id=f"doc-{uuid.uuid4().hex[:12]}", document_id=f"doc-{run_id}", blueprint_version=f"{blueprint.artifact_id}:v{blueprint.version}",
        run_id=run_id, version=1, status="draft", source_refs=refs, created_by=f"provider:{provider_name}:fallback", title=blueprint.title, sections=[section],
    )


def _mock_document_from_fixture(
    run_id: str,
    blueprint: LearningBlueprint,
    source_refs: list[str],
) -> LearningDocument:
    """Adapt the rich checked-in fixture to the current PDF/run provenance."""
    fixture = demo_document().model_copy(deep=True)
    refs = source_refs[:5]
    fixture.artifact_id = f"doc-{uuid.uuid4().hex[:12]}"
    fixture.document_id = f"doc-{run_id}"
    fixture.run_id = run_id
    fixture.version = 1
    fixture.status = "draft"
    fixture.blueprint_version = f"{blueprint.artifact_id}:v{blueprint.version}"
    fixture.created_by = "provider:mock"
    fixture.source_refs = refs
    for section in fixture.sections:
        for node in section.children:
            if hasattr(node, "source_refs"):
                node.source_refs = refs[: min(2, len(refs))]
    return fixture


def _metadata(response: Any) -> dict[str, Any]:
    return {"provider": response.provider, "model": response.model, "base_url": response.base_url, "config_version": response.config_version, "duration_ms": response.duration_ms, "usage": dict(response.usage)}


def _write_review_report(report: ReviewReport) -> Path:
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


def _review_report_for(document: LearningDocument, state: WorkflowState) -> ReviewReport:
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
    # A missing coverage report still indicates a legacy/fallback document
    # whose learning activity has not been checked.
    checks["learning_activity"] = "passed" if quiz_nodes or coverage_status == "passed" else "warning"
    if quiz_coverage is not None:
        checks["quiz_coverage"] = coverage_status
        checks["quiz_question_count"] = "passed" if coverage_status == "passed" else "blocking"
        existing_issue_ids = {issue.issue_id for issue in issues}
        for coverage_issue in list(getattr(quiz_coverage, "issues", []) or []):
            if coverage_issue.issue_id not in existing_issue_ids:
                issues.append(coverage_issue)
                existing_issue_ids.add(coverage_issue.issue_id)
    if quiz_coverage is None and not quiz_nodes:
        issues.append(ReviewIssue(
            issue_id="review-missing-learning-activity",
            category="coverage",
            severity="warning",
            layer="structure",
            message="文档当前没有可交互练习，且没有题目覆盖报告。",
            target_id=document.document_id,
            suggested_action="如学习目标需要练习，再由 Agent 根据教材证据生成题目并重新审核。",
        ))
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
    if malformed_quizzes or (quiz_coverage is not None and getattr(quiz_coverage, "status", "blocked") == "blocked"):
        checks["quiz_integrity"] = "blocking"
        if malformed_quizzes:
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
        checks["quiz_integrity"] = "passed" if quiz_nodes or coverage_status == "passed" else "warning"
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
        ) + (["如学习目标需要练习，再由 Agent 根据教材证据生成题目并重新审核。"] if quiz_coverage is None and not quiz_nodes else []),
    )
    path = _write_review_report(report)
    report.artifact_path = str(path.resolve())
    return report


def _assemble_document_from_artifacts(
    run_id: str,
    blueprint: LearningBlueprint,
    artifacts: list[ContentArtifact],
    provider_name: str,
    quiz_artifacts: list[QuizArtifact] | None = None,
) -> LearningDocument:
    by_unit: dict[str, list[ContentArtifact]] = {}
    for artifact in artifacts:
        for unit_id in artifact.knowledge_unit_ids:
            by_unit.setdefault(unit_id, []).append(artifact)
    quiz_by_unit: dict[str, list[QuizArtifact]] = {}
    for artifact in quiz_artifacts or []:
        for unit_id in artifact.knowledge_unit_ids:
            quiz_by_unit.setdefault(unit_id, []).append(artifact)
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
                continue
            for callout_index, raw_callout in enumerate(raw_callouts, start=1):
                # Accepted candidates passed this shape at the reflection
                # boundary. Retain a defensive guard for legacy/manual data.
                try:
                    callout = CalloutDraft.model_validate(raw_callout)
                except Exception:
                    continue
                children.append(CalloutNode(
                    id=f"{artifact.artifact_id}-callout-{callout_index}",
                    tone=callout.tone,
                    title=callout.title,
                    content=callout.content,
                    source_refs=list(artifact.source_refs),
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
                    source_refs=list(question.source_refs or quiz_artifact.source_refs),
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


def _blueprint_from_draft(
    *,
    draft: BlueprintDraft,
    state: WorkflowState,
    provider_name: str,
    fixture_adaptation: bool,
) -> tuple[LearningBlueprint, bool]:
    """Convert a validated model draft without repairing real-provider data."""

    # Re-validate here even when a test provider hands us a model instance.
    # This keeps the production boundary strict for enum values such as kind.
    draft = BlueprintDraft.model_validate(draft.model_dump(mode="python"))
    valid_refs = set(state["source_refs"])
    ordered_refs = list(state["source_refs"])
    units: list[KnowledgeUnit] = []
    adapted = False
    used_unit_ids: set[str] = set()
    for index, unit in enumerate(draft.knowledge_units[:12], start=1):
        if fixture_adaptation:
            refs = _normalize_refs(unit.source_refs, valid_refs)
            if not refs:
                refs = select_source_refs(
                    title=unit.title,
                    learning_objectives=unit.learning_objectives,
                    blocks=state["source_blocks"],
                ) or ordered_refs[:3]
                adapted = True
        else:
            refs = _lossless_refs(unit.source_refs)
        requested_id = (unit.knowledge_unit_id or "").strip()
        stable_id = requested_id or f"ku-{hashlib.sha256(unit.title.strip().encode('utf-8')).hexdigest()[:12]}"
        if stable_id in used_unit_ids:
            stable_id = f"{stable_id}-{index:02d}"
        used_unit_ids.add(stable_id)
        units.append(KnowledgeUnit(
            artifact_id=stable_id,
            run_id=state["run_id"],
            version=1,
            status="draft",
            source_refs=refs,
            created_by=f"provider:{provider_name}",
            title=unit.title,
            kind=unit.kind,
            learning_objectives=list(unit.learning_objectives),
            prerequisites=list(unit.prerequisites),
            related_unit_ids=list(unit.related_unit_ids),
        ))
    if not units:
        raise ProviderError("blueprint contains no knowledge units", category="schema")
    artifact_id = "bp-fixture-001" if fixture_adaptation else f"bp-{uuid.uuid4().hex[:12]}"
    blueprint = LearningBlueprint(
        artifact_id=artifact_id,
        run_id=state["run_id"],
        version=1,
        status="checking",
        source_refs=ordered_refs,
        created_by=f"provider:{provider_name}",
        title=draft.title or state["source_document"].title,
        knowledge_units=units,
    )
    return blueprint, adapted


def _provider_metadata(provider: ModelProvider) -> dict[str, Any]:
    return {
        "provider": getattr(provider, "provider", "unknown"),
        "model": getattr(provider, "model", "unknown"),
        "base_url": getattr(provider, "base_url", None),
        "config_version": getattr(provider, "config_version", "unknown"),
    }



def build_content_reflection_subgraph(
    provider: ModelProvider,
    *,
    max_attempts: int,
):
    """Build the bounded per-unit explanation reflection subgraph."""

    from annotation.workflow.content_reflection import build_content_reflection_subgraph as build

    return build(provider, max_attempts=max_attempts)


def build_minimal_graph(
    provider: ModelProvider | None = None,
    *,
    blueprint_max_attempts: int | None = None,
    content_reflection_max_attempts: int | None = None,
    fact_check_max_corrections: int | None = None,
    fact_check_max_claims_per_unit: int | None = None,
    fact_check_web_enabled: bool | None = None,
):
    """Build the top-level workflow graph while retaining the legacy import path."""

    from annotation.workflow.runtime import build_minimal_graph as build

    return build(
        provider,
        blueprint_max_attempts=blueprint_max_attempts,
        content_reflection_max_attempts=content_reflection_max_attempts,
        fact_check_max_corrections=fact_check_max_corrections,
        fact_check_max_claims_per_unit=fact_check_max_claims_per_unit,
        fact_check_web_enabled=fact_check_web_enabled,
    )


def run_minimal_workflow(
    *,
    provider: ModelProvider | None = None,
    run_id: str = "run-demo-001",
    blueprint: LearningBlueprint | None = None,
    pdf_path: str | Path | None = None,
    document_id: str | None = None,
    blueprint_max_attempts: int | None = None,
    content_reflection_max_attempts: int | None = None,
    fact_check_max_corrections: int | None = None,
    fact_check_max_claims_per_unit: int | None = None,
    fact_check_web_enabled: bool | None = None,
) -> WorkflowState:
    """Run the top-level workflow while retaining the legacy import path."""

    from annotation.workflow.runtime import run_minimal_workflow as run

    return run(
        provider=provider,
        run_id=run_id,
        blueprint=blueprint,
        pdf_path=pdf_path,
        document_id=document_id,
        blueprint_max_attempts=blueprint_max_attempts,
        content_reflection_max_attempts=content_reflection_max_attempts,
        fact_check_max_corrections=fact_check_max_corrections,
        fact_check_max_claims_per_unit=fact_check_max_claims_per_unit,
        fact_check_web_enabled=fact_check_web_enabled,
    )
