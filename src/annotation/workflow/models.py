"""Pydantic input models and LangGraph state contracts."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, field_validator

from annotation.domain.artifacts import (
    BlueprintAttemptTrace,
    BlueprintCheckResult,
    BlueprintLoopTrace,
    ContentArtifact,
    ContentAttemptTrace,
    ContentHardCheckResult,
    ContentTask,
    ContentUnitLoopTrace,
    ContextPack,
    KnowledgeUnit,
    LearningBlueprint,
    LearningDocument,
    QuizArtifact,
    ReviewIssue,
    ReviewReport,
    SourceBlock,
    SourceDocument,
)

class BlueprintDraftUnit(BaseModel):
    knowledge_unit_id: str | None = None
    title: str
    kind: Literal["concept", "formula", "theorem", "example", "skill"] = "concept"
    learning_objectives: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    teaching_materials: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    related_unit_ids: list[str] = Field(default_factory=list)


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


class ContentReflectionState(TypedDict, total=False):
    """State for one bounded explanation/reflection subgraph invocation."""

    run_id: str
    task: ContentTask
    unit: KnowledgeUnit
    context_pack: ContextPack
    valid_source_refs: list[str]
    max_attempts: int
    fixture_adaptation: bool
    attempt: int
    attempts: list[ContentAttemptTrace]
    generation_stage: str
    generation_prompt: str
    revision_prompt: str | None
    generation_raw_output: str
    generation_parsed_output: dict[str, Any] | None
    generation_status: str
    generation_error: str | None
    generation_error_category: str | None
    generation_retryable: bool
    generation_provider_metadata: dict[str, Any]
    candidate_draft: ContentDraft | None
    candidate_artifacts: list[ContentArtifact]
    hard_check: ContentHardCheckResult | None
    critic_prompt: str | None
    critic_raw_output: str
    critic_parsed_output: dict[str, Any] | None
    critic_status: str
    critic_error: str | None
    critic_error_category: str | None
    critic_retryable: bool
    critic_provider_metadata: dict[str, Any]
    critic_feedback: list[ReviewIssue]
    route: str
    content_loop_trace: ContentUnitLoopTrace
    final_draft: ContentDraft | None
    final_artifacts: list[ContentArtifact]


class WorkflowState(TypedDict, total=False):
    run_id: str
    document_id: str
    pdf_path: str
    source_document: SourceDocument
    source_blocks: list[SourceBlock]
    source_refs: list[str]
    blueprint: LearningBlueprint
    blueprint_check: BlueprintCheckResult
    blueprint_attempt: int
    blueprint_attempts: list[BlueprintAttemptTrace]
    blueprint_generation_status: str
    blueprint_generation_error: str
    blueprint_generation_error_category: str
    blueprint_generation_retryable: bool
    blueprint_generation_prompt: str
    blueprint_generation_raw_output: str
    blueprint_generation_parsed_output: dict[str, Any] | None
    blueprint_feedback: list[ReviewIssue]
    blueprint_route: str
    blueprint_loop_trace: BlueprintLoopTrace
    blueprint_trace_path: str
    blueprint_loop_trace_path: str
    blueprint_trace_id: str
    blueprint_preloaded: bool
    workflow_status: str
    content_tasks: list[ContentTask]
    context_packs: list[ContextPack]
    content_artifacts: list[ContentArtifact]
    content_loop_traces: list[ContentUnitLoopTrace]
    content_loop_summary: dict[str, Any]
    content_loop_status: str
    content_loop_trace_path: str
    quiz_artifacts: list[QuizArtifact]
    quiz_coverage_report: Any
    content_artifact_checks: dict[str, str]
    content_artifact_path: str
    quiz_artifact_path: str
    blueprint_artifact_path: str
    document_artifact_path: str
    run_manifest_path: str
    document: LearningDocument
    review_report: ReviewReport
    review_report_path: str
    provider_metadata: dict[str, Any]
    errors: list[str]
    warnings: list[str]


