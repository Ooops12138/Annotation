"""Pydantic input models and LangGraph state contracts."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    source_refs: list[str] = Field(default_factory=list)
    related_unit_ids: list[str] = Field(default_factory=list)


class BlueprintDraft(BaseModel):
    title: str
    knowledge_units: list[BlueprintDraftUnit] = Field(default_factory=list)


class DocumentDraft(BaseModel):
    title: str
    section_title: str
    explanation: str
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


class CalloutDraft(BaseModel):
    """An optional, intentionally separate learner-facing callout."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    tone: Literal["info", "warning", "success"] = "info"
    content: str = Field(min_length=1)


class ContentDraft(BaseModel):
    """One Markdown-first content response for a knowledge unit."""

    model_config = ConfigDict(extra="forbid")

    title: str
    content: str
    # Callouts are optional special material. All ordinary teaching content,
    # including follow-along steps, stays in the Markdown body.
    callouts: list[CalloutDraft] = Field(default_factory=list)
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


class FactCheckClaimDraft(BaseModel):
    """One claim selected for the bounded A-003 evidence pass."""

    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    kind: Literal["fact", "stance"]
    text: str = Field(min_length=1)
    query: str = Field(min_length=1)


class FactCheckClaimsDraft(BaseModel):
    """Structured claim extraction output for one knowledge unit."""

    model_config = ConfigDict(extra="forbid")

    claims: list[FactCheckClaimDraft] = Field(default_factory=list)


class FactCheckAssessmentDraft(BaseModel):
    """One evidence judgement returned by the A-003 assessor."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    verdict: Literal["supported", "contradicted", "insufficient", "external_conflict", "stance"]
    judgement: str = Field(min_length=1)
    sufficient_textbook_evidence: bool = False


class FactCheckAssessmentsDraft(BaseModel):
    """Batch evidence judgement output for the claims in one knowledge unit."""

    model_config = ConfigDict(extra="forbid")

    assessments: list[FactCheckAssessmentDraft] = Field(default_factory=list)


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
    candidate_artifact: ContentArtifact | None
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
    fact_check_artifact: Any
    fact_check_issues: list[ReviewIssue]
    fact_check_summary: dict[str, Any]
    fact_check_status: str
    fact_check_artifact_path: str
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


