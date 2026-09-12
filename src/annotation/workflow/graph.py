"""PDF-driven LangGraph workflow for the runnable POC demo."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, field_validator

from annotation.domain.artifacts import (
    BlueprintCheckResult,
    CalloutNode,
    ContentArtifact,
    ContentTask,
    ContextPack,
    DocumentSection,
    FormulaNode,
    KnowledgeUnit,
    LearningBlueprint,
    LearningDocument,
    MarkdownNode,
    QuizNode,
    ReviewIssue,
    ReviewReport,
    SourceBlock,
    SourceDocument,
)
from annotation.fixtures.demo import demo_document
from annotation.config import STORAGE_DIR, first_book_pdf
from annotation.ingestion.pdf_parser import parse_pdf
from annotation.ingestion.pdf_parser import extraction_warnings
from annotation.workflow.blueprint_checks import validate_blueprint
from annotation.providers import (
    ModelProvider,
    MockProvider,
    ProviderError,
    StructuredGenerationRequest,
)
from annotation.prompt_loader import load_prompt
from annotation.workflow.content import build_context_pack, render_context_pack, select_source_refs, write_content_run_artifact


class BlueprintDraftUnit(BaseModel):
    title: str
    kind: str = "concept"
    learning_objectives: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    teaching_materials: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class BlueprintDraft(BaseModel):
    title: str
    knowledge_units: list[BlueprintDraftUnit] = Field(default_factory=list)


class DocumentDraft(BaseModel):
    title: str
    section_title: str
    explanation: str
    formula_latex: str = r"\lim_{x \to a} f(x)=L"
    quiz_question: str
    quiz_options: list[str] = Field(default_factory=list)
    quiz_answer: str
    quiz_explanation: str
    source_refs: list[str] = Field(default_factory=list)

    @field_validator("quiz_answer", mode="before")
    @classmethod
    def coerce_answer(cls, value: Any) -> str:
        return str(value)

    @field_validator("source_refs", mode="before")
    @classmethod
    def coerce_source_refs(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]


class ContentDraft(BaseModel):
    """Small structured response for one T-006 task."""

    title: str
    content: str
    material_role: str = "explanation"
    formula_latex: str | None = None
    teaching_material: str | None = None
    source_refs: list[str] = Field(default_factory=list)

    @field_validator("source_refs", mode="before")
    @classmethod
    def coerce_source_refs(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]


class WorkflowState(TypedDict, total=False):
    run_id: str
    pdf_path: str
    source_document: SourceDocument
    source_blocks: list[SourceBlock]
    source_refs: list[str]
    blueprint: LearningBlueprint
    blueprint_check: BlueprintCheckResult
    content_tasks: list[ContentTask]
    context_packs: list[ContextPack]
    content_artifacts: list[ContentArtifact]
    content_artifact_checks: dict[str, str]
    content_artifact_path: str
    document: LearningDocument
    review_report: ReviewReport
    review_report_path: str
    provider_metadata: dict[str, Any]
    errors: list[str]
    warnings: list[str]


def _default_pdf() -> Path:
    return first_book_pdf()


def _source_context(blocks: list[SourceBlock], limit: int = 35) -> str:
    return "\n".join(
        f"[{block.source_ref}] {re.sub(r'\\s+', ' ', block.text).strip()[:500]}"
        for block in blocks[:limit]
        if block.text.strip()
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
        FormulaNode(id="formula-main", latex=r"\lim_{x \to a} f(x)=L", source_refs=refs),
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
    checks: dict[str, str] = {
        "document_structure": "passed" if document.sections and any(section.children for section in document.sections) else "blocking",
        "source_traceability": "passed" if document.source_refs and set(document.source_refs).issubset(valid_refs) else "blocking",
    }
    nodes = [node for section in document.sections for node in section.children]
    quiz_nodes = [node for node in nodes if getattr(node, "type", None) == "quiz"]
    checks["learning_activity"] = "passed" if quiz_nodes else "warning"
    if not quiz_nodes:
        issues.append(ReviewIssue(
            issue_id="review-missing-learning-activity",
            category="coverage",
            severity="warning",
            layer="structure",
            message="文档当前没有可交互的练习；T-007 已后置，发布前应补齐与知识单元对应的测验。",
            target_id=document.document_id,
            suggested_action="补充可追溯题目、答案和解析后重新审核。",
        ))
    malformed_quizzes = [
        getattr(node, "id", "unknown")
        for node in quiz_nodes
        if len(getattr(node, "options", []) or []) < 2 or getattr(node, "answer", None) not in (getattr(node, "options", []) or [])
    ]
    if malformed_quizzes:
        checks["quiz_integrity"] = "blocking"
        issues.append(ReviewIssue(
            issue_id="review-invalid-quiz",
            category="logic",
            severity="blocking",
            layer="structure",
            message=f"测验节点的选项或答案不完整：{', '.join(malformed_quizzes)}。",
            target_id=document.document_id,
            suggested_action="确认答案属于选项，并补齐至少两个可选项。",
        ))
    else:
        checks["quiz_integrity"] = "passed" if quiz_nodes else "warning"
    malformed_formulas = [
        getattr(node, "id", "unknown")
        for node in nodes
        if getattr(node, "type", None) == "formula" and not str(getattr(node, "latex", "")).strip()
    ]
    if malformed_formulas:
        checks["formula_integrity"] = "blocking"
        issues.append(ReviewIssue(
            issue_id="review-empty-formula",
            category="formula",
            severity="blocking",
            layer="structure",
            message=f"公式节点缺少 LaTeX 内容：{', '.join(malformed_formulas)}。",
            target_id=document.document_id,
            suggested_action="补充公式或将该节点退回内容生成阶段。",
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
        ) + (["补齐测验覆盖并重新运行审核。"] if not quiz_nodes else []),
    )
    path = _write_review_report(report)
    report.artifact_path = str(path.resolve())
    return report


def _merge_messages(existing: list[str] | None, new: list[str]) -> list[str]:
    return list(dict.fromkeys([*(existing or []), *new]))


def _normalize_refs(values: list[str], valid_refs: set[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        candidate = value.strip().strip("[]")
        if candidate in valid_refs and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _plan_content_tasks(run_id: str, blueprint: LearningBlueprint) -> list[ContentTask]:
    tasks: list[ContentTask] = []
    blueprint_version = f"{blueprint.artifact_id}:v{blueprint.version}"
    for index, unit in enumerate(blueprint.knowledge_units, start=1):
        criteria = [
            "覆盖该知识单元的学习目标",
            "只使用 ContextPack 中可定位的教材证据",
            "说明与直接前置知识的必要衔接" if unit.prerequisites else "使用适合初学者的分步解释",
        ]
        tasks.append(ContentTask(
            task_id=f"task-{run_id}-{index:03d}",
            run_id=run_id,
            blueprint_version=blueprint_version,
            knowledge_unit_id=unit.artifact_id,
            source_refs=list(unit.source_refs),
            acceptance_criteria=criteria,
        ))
    return tasks


def _mock_content_draft(unit: KnowledgeUnit, pack: ContextPack) -> ContentDraft:
    refs = pack.source_refs[:3]
    evidence = " ".join(excerpt.text for excerpt in pack.excerpts[:2])
    prerequisite_note = (
        f"本单元建立在“{'、'.join(unit.prerequisites)}”之上。"
        if unit.prerequisites else ""
    )
    return ContentDraft(
        title=unit.title,
        content=(
            f"本节围绕“{unit.title}”展开。{prerequisite_note}"
            f"学习目标是：{'；'.join(unit.learning_objectives) or '掌握本单元的基本含义和用法'}。"
            f"教材证据摘录：{evidence or '当前没有可用的教材摘录，需要人工审核。'}"
        ),
        material_role="explanation",
        source_refs=refs,
        teaching_material=(
            f"可配合教学材料“{'、'.join(unit.teaching_materials)}”进行练习或复述。"
            if unit.teaching_materials else None
        ),
    )


def _content_artifact_from_draft(
    *,
    draft: ContentDraft,
    task: ContentTask,
    unit: KnowledgeUnit,
    pack: ContextPack,
    run_id: str,
    provider_name: str,
    ) -> ContentArtifact:
    valid_refs = set(pack.source_refs)
    refs = _normalize_refs(draft.source_refs, valid_refs) or list(pack.source_refs)
    role = draft.material_role if draft.material_role in {"explanation", "example", "proof", "bridge", "supplement"} else "explanation"
    content = draft.content.strip()
    if draft.formula_latex:
        content = f"{content}\n\n公式：$${draft.formula_latex}$$"
    return ContentArtifact(
        artifact_id=f"content-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        version=1,
        status="draft",
        source_refs=refs,
        created_by=f"provider:{provider_name}",
        content_type="explanation" if role in {"explanation", "bridge", "proof", "supplement"} else "example",
        knowledge_unit_ids=[unit.artifact_id],
        title=draft.title or unit.title,
        material_role=role,
        task_id=task.task_id,
        context_pack_id=pack.context_pack_id,
        prompt_version="generate_content_artifact:v1",
        metadata={
            "learning_objectives": list(unit.learning_objectives),
            "teaching_materials": list(unit.teaching_materials),
            "formula_latex": draft.formula_latex,
            "omitted_source_refs": list(pack.omitted_source_refs),
            "retrieval_strategy": list(pack.retrieval_strategy),
            "source_snapshot": pack.source_snapshot,
        },
        content=content,
    )


def _teaching_material_artifact(
    *,
    draft: ContentDraft,
    parent: ContentArtifact,
    task: ContentTask,
    unit: KnowledgeUnit,
    pack: ContextPack,
    run_id: str,
    provider_name: str,
) -> ContentArtifact:
    """Create a separately traceable teaching-material artifact."""

    role = "example" if unit.kind == "example" else "proof" if unit.kind == "theorem" else "bridge"
    material = (draft.teaching_material or "").strip() or (
        f"教学材料建议：围绕“{unit.title}”使用教材中的“{'、'.join(unit.teaching_materials)}”进行讲解、复述或练习。"
    )
    return ContentArtifact(
        artifact_id=f"content-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        version=1,
        status="accepted" if pack.source_refs else "blocked",
        source_refs=list(pack.source_refs),
        created_by=f"provider:{provider_name}",
        content_type="example" if role == "example" else "explanation",
        knowledge_unit_ids=[unit.artifact_id],
        title=f"{unit.title}：教学材料",
        material_role=role,
        task_id=task.task_id,
        context_pack_id=pack.context_pack_id,
        prompt_version="generate_content_artifact:v1",
        parent_artifact_id=parent.artifact_id,
        metadata={
            "teaching_materials": list(unit.teaching_materials),
            "material_kind": role,
            "source_snapshot": pack.source_snapshot,
        },
        content=material,
    )


def _unit_context(unit: KnowledgeUnit) -> str:
    return json.dumps(
        {
            "knowledge_unit_id": unit.artifact_id,
            "title": unit.title,
            "kind": unit.kind,
            "learning_objectives": unit.learning_objectives,
            "prerequisites": unit.prerequisites,
            "teaching_materials": unit.teaching_materials,
            "source_refs": unit.source_refs,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _assemble_document_from_artifacts(
    run_id: str,
    blueprint: LearningBlueprint,
    artifacts: list[ContentArtifact],
    provider_name: str,
) -> LearningDocument:
    by_unit: dict[str, list[ContentArtifact]] = {}
    for artifact in artifacts:
        for unit_id in artifact.knowledge_unit_ids:
            by_unit.setdefault(unit_id, []).append(artifact)
    sections: list[DocumentSection] = []
    units = {unit.artifact_id: unit for unit in blueprint.knowledge_units}
    for index, unit in enumerate(blueprint.knowledge_units, start=1):
        unit_artifacts = by_unit.get(unit.artifact_id, [])
        children: list[Any] = []
        for artifact in unit_artifacts:
            if artifact.material_role == "example":
                children.append(ExampleNode(
                    id=f"{artifact.artifact_id}-example",
                    title=artifact.title or f"{unit.title}：练习材料",
                    problem=f"围绕“{unit.title}”完成一次复述、辨析或计算。",
                    solution=artifact.content,
                    source_refs=list(artifact.source_refs),
                ))
            elif artifact.material_role in {"bridge", "proof", "supplement"}:
                children.append(CalloutNode(
                    id=f"{artifact.artifact_id}-callout",
                    tone="info",
                    title=artifact.title or "教学材料",
                    content=artifact.content,
                ))
            else:
                children.append(MarkdownNode(id=f"{artifact.artifact_id}-markdown", content=artifact.content, source_refs=list(artifact.source_refs)))
            formula_latex = artifact.metadata.get("formula_latex")
            if formula_latex:
                children.append(FormulaNode(id=f"{artifact.artifact_id}-formula", latex=str(formula_latex), source_refs=list(artifact.source_refs)))
        if unit.learning_objectives:
            children.append(CalloutNode(
                id=f"unit-{unit.artifact_id}-objectives",
                tone="info",
                title="学习目标",
                content="；".join(unit.learning_objectives),
            ))
        sections.append(DocumentSection(id=f"section-{index:03d}", title=unit.title, children=children))
    refs: list[str] = []
    for artifact in artifacts:
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


def build_minimal_graph(provider: ModelProvider | None = None):
    model_provider = provider or MockProvider()

    def ingest(state: WorkflowState) -> dict[str, Any]:
        run_id = state.get("run_id", f"run-{uuid.uuid4().hex[:10]}")
        pdf_path = Path(state.get("pdf_path") or _default_pdf())
        source_document, blocks = parse_pdf(pdf_path, run_id=run_id)
        warnings = extraction_warnings(blocks)
        return {"run_id": run_id, "pdf_path": str(pdf_path), "source_document": source_document, "source_blocks": blocks, "source_refs": [block.source_ref for block in blocks], "warnings": warnings}

    def load_or_create_blueprint(state: WorkflowState) -> dict[str, Any]:
        if state.get("blueprint"):
            return {}
        prompt = load_prompt(
            "load_or_create_blueprint",
            TEXTBOOK_CONTEXT=_source_context(state["source_blocks"], 12),
        )
        try:
            response = model_provider.generate_structured(StructuredGenerationRequest(prompt=prompt, schema=BlueprintDraft, max_output_tokens=3000, metadata={"agent": "load_or_create_blueprint", "run_id": state["run_id"]}))
            draft = response.value
            valid_refs = set(state["source_refs"])
            ordered_refs = state["source_refs"]
            units = [KnowledgeUnit(
                artifact_id=f"ku-{uuid.uuid4().hex[:12]}", run_id=state["run_id"], version=1, status="draft",
                source_refs=(
                    _normalize_refs(unit.source_refs, valid_refs)
                    or select_source_refs(
                        title=unit.title,
                        learning_objectives=unit.learning_objectives,
                        teaching_materials=unit.teaching_materials,
                        blocks=state["source_blocks"],
                    )
                    or ordered_refs[:3]
                ), created_by=f"provider:{response.provider}",
                title=unit.title, kind=unit.kind if unit.kind in {"concept", "formula", "theorem", "example", "skill"} else "concept",
                learning_objectives=unit.learning_objectives, prerequisites=unit.prerequisites,
                teaching_materials=unit.teaching_materials,
            ) for unit in draft.knowledge_units[:12]]
            if not units:
                raise ProviderError("blueprint contains no knowledge units", category="schema")
            artifact_id = "bp-fixture-001" if response.provider == "mock" else f"bp-{uuid.uuid4().hex[:12]}"
            blueprint = LearningBlueprint(artifact_id=artifact_id, run_id=state["run_id"], version=1, status="checking", source_refs=ordered_refs, created_by=f"provider:{response.provider}", title=draft.title or state["source_document"].title, knowledge_units=units)
            check = validate_blueprint(blueprint, valid_source_refs=valid_refs)
            blueprint.issues = check.issues
            blueprint.status = "needs_revision" if check.status == "needs_revision" else "accepted"
            if check.status == "needs_revision":
                fallback = _fallback_blueprint(state["run_id"], state["source_document"], state["source_blocks"])
                fallback.issues = check.issues
                return {
                    "blueprint": fallback,
                    "blueprint_check": check,
                    "provider_metadata": _metadata(response),
                    "warnings": ["blueprint_quality_gate: generated blueprint needs revision; fallback blueprint used"],
                }
            return {"blueprint": blueprint, "blueprint_check": check, "provider_metadata": _metadata(response)}
        except Exception as exc:
            fallback = _fallback_blueprint(state["run_id"], state["source_document"], state["source_blocks"])
            return {"blueprint": fallback, "warnings": [f"blueprint_generation_fallback: {exc}"]}

    def plan_content_tasks(state: WorkflowState) -> dict[str, Any]:
        return {"content_tasks": _plan_content_tasks(state["run_id"], state["blueprint"])}

    def build_context_packs(state: WorkflowState) -> dict[str, Any]:
        blueprint = state["blueprint"]
        units = {unit.artifact_id: unit for unit in blueprint.knowledge_units}
        capabilities = getattr(model_provider, "capabilities", None)
        context_window = getattr(capabilities, "context_window", None)
        packs: list[ContextPack] = []
        warnings: list[str] = []
        for task in state.get("content_tasks", []):
            unit = units.get(task.knowledge_unit_id)
            if not unit:
                warnings.append(f"content_task_missing_unit: {task.task_id}")
                continue
            pack = build_context_pack(task, unit, blueprint, state["source_blocks"], context_window=context_window)
            packs.append(pack)
            if pack.omitted_source_refs:
                warnings.append(f"context_pack_omitted_sources:{task.task_id}:{','.join(pack.omitted_source_refs)}")
        return {"context_packs": packs, "warnings": _merge_messages(state.get("warnings"), warnings)}

    def generate_content_artifacts(state: WorkflowState) -> dict[str, Any]:
        blueprint = state["blueprint"]
        units = {unit.artifact_id: unit for unit in blueprint.knowledge_units}
        packs = {pack.task_id: pack for pack in state.get("context_packs", [])}
        artifacts: list[ContentArtifact] = []
        checks: dict[str, str] = {}
        warnings: list[str] = []
        provider_metadata: dict[str, Any] = dict(state.get("provider_metadata", {}))
        for task in state.get("content_tasks", []):
            task.status = "generating"
            unit = units.get(task.knowledge_unit_id)
            pack = packs.get(task.task_id)
            if not unit or not pack:
                checks[task.task_id] = "blocked"
                task.status = "blocked"
                warnings.append(f"content_task_blocked:{task.task_id}")
                continue
            if pack.omitted_source_refs:
                checks[task.task_id] = "blocked"
                task.status = "blocked"
                warnings.append(f"content_task_evidence_over_budget:{task.task_id}:{','.join(pack.omitted_source_refs)}")
                continue
            prompt = load_prompt(
                "generate_content_artifact",
                KNOWLEDGE_UNIT_CONTEXT=_unit_context(unit),
                CONTEXT_PACK=render_context_pack(pack),
                ACCEPTANCE_CRITERIA=json.dumps(task.acceptance_criteria, ensure_ascii=False),
            )
            try:
                if getattr(model_provider, "provider", "") == "mock":
                    draft = _mock_content_draft(unit, pack)
                    response_metadata = {"provider": "mock", "model": getattr(model_provider, "model", "fixture-model"), "base_url": None, "config_version": getattr(model_provider, "config_version", "mock-v1"), "duration_ms": 0, "usage": {}}
                else:
                    provider_limit = getattr(getattr(model_provider, "capabilities", None), "max_output_tokens", None)
                    response = model_provider.generate_structured(StructuredGenerationRequest(
                        prompt=prompt,
                        schema=ContentDraft,
                        max_output_tokens=min(2200, provider_limit) if provider_limit else 2200,
                        metadata={"agent": "generate_content_artifact", "run_id": state["run_id"], "task_id": task.task_id, "context_pack_id": pack.context_pack_id},
                    ))
                    draft = response.value
                    response_metadata = _metadata(response)
                artifact = _content_artifact_from_draft(draft=draft, task=task, unit=unit, pack=pack, run_id=state["run_id"], provider_name=response_metadata["provider"])
                artifact.status = "accepted" if artifact.content and artifact.source_refs else "needs_revision"
                if not artifact.source_refs:
                    artifact.issues.append(ReviewIssue(issue_id=f"content-missing-sources-{task.task_id}", category="source", severity="blocking", message="内容 artifact 没有可回溯的来源。", target_id=artifact.artifact_id))
                    artifact.status = "blocked"
                artifacts.append(artifact)
                if unit.teaching_materials:
                    artifacts.append(_teaching_material_artifact(
                        draft=draft,
                        parent=artifact,
                        task=task,
                        unit=unit,
                        pack=pack,
                        run_id=state["run_id"],
                        provider_name=response_metadata["provider"],
                    ))
                checks[task.task_id] = artifact.status
                task.status = artifact.status
                provider_metadata = response_metadata
            except Exception as exc:
                checks[task.task_id] = "blocked"
                task.status = "blocked"
                warnings.append(f"content_generation_fallback:{task.task_id}:{exc}")
        artifact_path = write_content_run_artifact(
            run_id=state["run_id"],
            blueprint_version=f"{blueprint.artifact_id}:v{blueprint.version}",
            tasks=state.get("content_tasks", []),
            context_packs=state.get("context_packs", []),
            artifacts=artifacts,
            checks=checks,
            provider_metadata=provider_metadata,
            root=STORAGE_DIR / "artifacts" / "content",
        )
        return {"content_tasks": state.get("content_tasks", []), "content_artifacts": artifacts, "content_artifact_checks": checks, "content_artifact_path": str(artifact_path.resolve()), "provider_metadata": provider_metadata, "warnings": _merge_messages(state.get("warnings"), warnings)}

    def assemble_document_ir(state: WorkflowState) -> dict[str, Any]:
        artifacts = [artifact for artifact in state.get("content_artifacts", []) if artifact.status == "accepted"]
        if not artifacts:
            return {"document": _fallback_document(state["run_id"], state["blueprint"], state["source_document"], state["source_blocks"], model_provider.provider)}
        # Keep the rich offline fixture as a renderer regression baseline while
        # exposing the new per-unit artifacts in workflow state.  Real providers
        # are assembled directly from the accepted T-006 artifacts.
        if model_provider.provider == "mock":
            refs = []
            for artifact in artifacts:
                refs.extend(ref for ref in artifact.source_refs if ref not in refs)
            return {"document": _mock_document_from_fixture(state["run_id"], state["blueprint"], refs)}
        return {"document": _assemble_document_from_artifacts(state["run_id"], state["blueprint"], artifacts, model_provider.provider)}

    def validate_document_ir(state: WorkflowState) -> dict[str, Any]:
        try:
            LearningDocument.model_validate(state["document"].model_dump())
            return {}
        except Exception as exc:
            return {"errors": [f"document_validation: {exc}"]}

    def review(state: WorkflowState) -> dict[str, Any]:
        document = state["document"]
        issues = list(document.issues)
        valid_refs = set(state["source_refs"])
        tasks = state.get("content_tasks", [])
        artifacts = state.get("content_artifacts", [])
        accepted_task_ids = {artifact.task_id for artifact in artifacts if artifact.status == "accepted" and artifact.task_id}
        if tasks and len(accepted_task_ids) != len(tasks):
            missing = [task.task_id for task in tasks if task.task_id not in accepted_task_ids]
            issues.append(ReviewIssue(issue_id="review-content-task-coverage", category="coverage", severity="blocking", message=f"有内容任务未产出可接受 artifact：{', '.join(missing)}", target_id=document.document_id))
        for artifact in artifacts:
            if artifact.status == "blocked":
                issues.append(ReviewIssue(issue_id=f"review-blocked-content-{artifact.artifact_id}", category="coverage", severity="blocking", message="内容 artifact 被阻塞，不能进入发布文档。", target_id=artifact.artifact_id))
            if not set(artifact.source_refs).issubset(valid_refs):
                issues.append(ReviewIssue(issue_id=f"review-invalid-content-sources-{artifact.artifact_id}", category="source", severity="blocking", message="内容 artifact 包含无法回溯到本次教材导入的来源引用。", target_id=artifact.artifact_id))
        if not document.source_refs:
            issues.append(ReviewIssue(issue_id="review-missing-sources", category="source", severity="blocking", message="文档缺少教材来源引用。", target_id=document.document_id))
        elif not set(document.source_refs).issubset(valid_refs):
            issues.append(ReviewIssue(issue_id="review-invalid-sources", category="source", severity="blocking", message="文档包含无法回溯到本次教材导入的来源引用。", target_id=document.document_id))
        if not document.sections or not any(section.children for section in document.sections):
            issues.append(ReviewIssue(issue_id="review-empty-document", category="coverage", severity="blocking", message="文档没有可呈现内容。", target_id=document.document_id))
        if state.get("warnings"):
            issues.append(ReviewIssue(
                issue_id="review-generation-warning",
                category="uncertainty",
                severity="warning",
                layer="fact",
                message="；".join(state["warnings"]),
                target_id=document.document_id,
                suggested_action="对涉及的教材片段、公式候选和生成内容进行人工抽样复核。",
            ))
        document.issues = issues
        document.status = "blocked" if any(issue.severity == "blocking" for issue in issues) else "accepted"
        report = _review_report_for(document, {**state, "source_refs": list(valid_refs)})
        document.review_report_id = report.report_id
        return {"document": document, "review_report": report, "review_report_path": report.artifact_path or ""}

    def assemble(state: WorkflowState) -> dict[str, Any]:
        document = state["document"]
        if not state.get("errors") and document.status == "accepted":
            document.status = "published"
        return {"document": document}

    graph = StateGraph(WorkflowState)
    for name, node in {"ingest": ingest, "load_or_create_blueprint": load_or_create_blueprint, "plan_content_tasks": plan_content_tasks, "build_context_packs": build_context_packs, "generate_content_artifacts": generate_content_artifacts, "assemble_document_ir": assemble_document_ir, "validate_document_ir": validate_document_ir, "review": review, "assemble": assemble}.items():
        graph.add_node(name, node)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "load_or_create_blueprint")
    graph.add_edge("load_or_create_blueprint", "plan_content_tasks")
    graph.add_edge("plan_content_tasks", "build_context_packs")
    graph.add_edge("build_context_packs", "generate_content_artifacts")
    graph.add_edge("generate_content_artifacts", "assemble_document_ir")
    graph.add_edge("assemble_document_ir", "validate_document_ir")
    graph.add_edge("validate_document_ir", "review")
    graph.add_edge("review", "assemble")
    graph.add_edge("assemble", END)
    return graph.compile()


def run_minimal_workflow(*, provider: ModelProvider | None = None, run_id: str = "run-demo-001", blueprint: LearningBlueprint | None = None, pdf_path: str | Path | None = None) -> WorkflowState:
    initial: WorkflowState = {"run_id": run_id}
    if blueprint:
        initial["blueprint"] = blueprint
    if pdf_path:
        initial["pdf_path"] = str(pdf_path)
    return build_minimal_graph(provider).invoke(initial)
