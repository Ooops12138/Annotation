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
    DocumentSection,
    FormulaNode,
    KnowledgeUnit,
    LearningBlueprint,
    LearningDocument,
    MarkdownNode,
    QuizNode,
    ReviewIssue,
    SourceBlock,
    SourceDocument,
)
from annotation.fixtures.demo import demo_document
from annotation.config import first_book_pdf
from annotation.ingestion.pdf_parser import parse_pdf
from annotation.ingestion.pdf_parser import extraction_warnings
from annotation.workflow.blueprint_checks import validate_blueprint
from annotation.providers import (
    ModelProvider,
    MockProvider,
    ProviderError,
    StructuredGenerationRequest,
)


class BlueprintDraftUnit(BaseModel):
    title: str
    kind: str = "concept"
    learning_objectives: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
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


class WorkflowState(TypedDict, total=False):
    run_id: str
    pdf_path: str
    source_document: SourceDocument
    source_blocks: list[SourceBlock]
    source_refs: list[str]
    blueprint: LearningBlueprint
    blueprint_check: BlueprintCheckResult
    document: LearningDocument
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


def _normalize_refs(values: list[str], valid_refs: set[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        candidate = value.strip().strip("[]")
        if candidate in valid_refs and candidate not in normalized:
            normalized.append(candidate)
    return normalized


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
        prompt = "Return ONLY valid JSON, with no Markdown or explanation. You are a textbook understanding assistant. Use only the textbook excerpts below; do not invent facts. Schema: {title: string, knowledge_units: [{title: string, kind: concept|formula|theorem|example|skill, learning_objectives: string[], prerequisites: string[], source_refs: string[]}]}. Create at least 3 units. Every source_refs item must be copied exactly from the bracketed source IDs.\n\n" + _source_context(state["source_blocks"], 12)
        try:
            response = model_provider.generate_structured(StructuredGenerationRequest(prompt=prompt, schema=BlueprintDraft, max_output_tokens=3000, metadata={"agent": "load_or_create_blueprint", "run_id": state["run_id"]}))
            draft = response.value
            valid_refs = set(state["source_refs"])
            ordered_refs = state["source_refs"]
            units = [KnowledgeUnit(
                artifact_id=f"ku-{uuid.uuid4().hex[:12]}", run_id=state["run_id"], version=1, status="draft",
                source_refs=_normalize_refs(unit.source_refs, valid_refs) or ordered_refs[:3], created_by=f"provider:{response.provider}",
                title=unit.title, kind=unit.kind if unit.kind in {"concept", "formula", "theorem", "example", "skill"} else "concept",
                learning_objectives=unit.learning_objectives, prerequisites=unit.prerequisites,
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

    def generate_document_ir(state: WorkflowState) -> dict[str, Any]:
        blueprint = state["blueprint"]
        # The artifact keeps every imported source reference for provenance, but
        # sending all 1200+ refs back to the model needlessly consumes its
        # context window.  Keep the generation prompt to the instructional
        # blueprint fields and the refs attached to each unit.
        blueprint_context = {
            "title": blueprint.title,
            "knowledge_units": [
                {
                    "title": unit.title,
                    "kind": unit.kind,
                    "learning_objectives": unit.learning_objectives,
                    "prerequisites": unit.prerequisites,
                    "source_refs": unit.source_refs,
                }
                for unit in blueprint.knowledge_units
            ],
        }
        prompt = "Return ONLY valid JSON, with no Markdown or explanation. You are a learning-document generator. Use only the Learning Blueprint and textbook excerpts below. Do not output HTML or JavaScript. Schema: {title: string, section_title: string, explanation: string, formula_latex: string, quiz_question: string, quiz_options: string[], quiz_answer: string, quiz_explanation: string, source_refs: string[]}. Every source_refs item must be copied exactly from the bracketed source IDs.\n\nBlueprint:\n" + json.dumps(blueprint_context, ensure_ascii=False, separators=(",", ":")) + "\n\nTextbook excerpts:\n" + _source_context(state["source_blocks"], 12)
        try:
            # DeepSeek's reasoning models count hidden reasoning tokens against
            # max_tokens.  A 3000-token budget can end with a truncated JSON
            # document even when the visible answer is short; leave enough
            # room for both reasoning and the required structured payload.
            response = model_provider.generate_structured(StructuredGenerationRequest(prompt=prompt, schema=DocumentDraft, max_output_tokens=6000, metadata={"agent": "generate_document_ir", "run_id": state["run_id"]}))
            draft = response.value
            refs = _normalize_refs(draft.source_refs, set(state["source_refs"])) or blueprint.source_refs[:5]
            if not refs:
                refs = state["source_refs"][:5]
            if response.provider == "mock":
                return {"document": _mock_document_from_fixture(state["run_id"], blueprint, refs), "provider_metadata": _metadata(response)}
            document = LearningDocument(
                artifact_id=f"doc-{uuid.uuid4().hex[:12]}", document_id=f"doc-{state['run_id']}", blueprint_version=f"{blueprint.artifact_id}:v{blueprint.version}", run_id=state["run_id"], version=1, status="draft", source_refs=refs, created_by=f"provider:{response.provider}", title=draft.title or blueprint.title,
                sections=[DocumentSection(id="section-main", title=draft.section_title or blueprint.title, children=[
                    MarkdownNode(id="explanation-main", content=draft.explanation, source_refs=refs), FormulaNode(id="formula-main", latex=draft.formula_latex, source_refs=refs),
                    CalloutNode(id="review-note", tone="warning", title="审核提示", content="生成内容必须结合教材原文完成人工复核。"),
                    QuizNode(id="quiz-main", question=draft.quiz_question, options=draft.quiz_options[:6] or [draft.quiz_answer], answer=draft.quiz_answer, explanation=draft.quiz_explanation, source_refs=refs),
                ])],
            )
            return {"document": document, "provider_metadata": _metadata(response)}
        except Exception as exc:
            return {"document": _fallback_document(state["run_id"], blueprint, state["source_document"], state["source_blocks"], model_provider.provider), "warnings": [f"document_generation_fallback: {exc}"]}

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
        if not document.source_refs:
            issues.append(ReviewIssue(issue_id="review-missing-sources", category="source", severity="blocking", message="文档缺少教材来源引用。", target_id=document.document_id))
        elif not set(document.source_refs).issubset(valid_refs):
            issues.append(ReviewIssue(issue_id="review-invalid-sources", category="source", severity="blocking", message="文档包含无法回溯到本次教材导入的来源引用。", target_id=document.document_id))
        if not document.sections or not any(section.children for section in document.sections):
            issues.append(ReviewIssue(issue_id="review-empty-document", category="coverage", severity="blocking", message="文档没有可呈现内容。", target_id=document.document_id))
        if state.get("warnings"):
            issues.append(ReviewIssue(issue_id="review-generation-warning", category="uncertainty", severity="warning", message="；".join(state["warnings"]), target_id=document.document_id))
        document.issues = issues
        document.status = "blocked" if any(issue.severity == "blocking" for issue in issues) else "accepted"
        return {"document": document}

    def assemble(state: WorkflowState) -> dict[str, Any]:
        document = state["document"]
        if not state.get("errors") and document.status == "accepted":
            document.status = "published"
        return {"document": document}

    graph = StateGraph(WorkflowState)
    for name, node in {"ingest": ingest, "load_or_create_blueprint": load_or_create_blueprint, "generate_document_ir": generate_document_ir, "validate_document_ir": validate_document_ir, "review": review, "assemble": assemble}.items():
        graph.add_node(name, node)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "load_or_create_blueprint")
    graph.add_edge("load_or_create_blueprint", "generate_document_ir")
    graph.add_edge("generate_document_ir", "validate_document_ir")
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
