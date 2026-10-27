"""Pydantic input models and LangGraph state contracts."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from annotation.domain.artifacts import (
    BlueprintAttemptTrace,
    BlueprintCheckResult,
    BlueprintLoopTrace,
    ContentArtifact,
    EvidenceNote,
    ContentAttemptTrace,
    ContentHardCheckResult,
    ContentTask,
    ContentUnitLoopTrace,
    ContextPack,
    InteractiveComponentArtifact,
    InteractiveComponentAttemptTrace,
    InteractiveComponentHardCheckResult,
    InteractiveComponentLoopTrace,
    InteractiveComponentPlan,
    InteractiveComponentSandboxReport,
    InteractiveComponentSpec,
    InteractiveComponentTask,
    KnowledgeUnit,
    LearningBlueprint,
    LearningDocument,
    QuizArtifact,
    ReviewIssue,
    ReviewReport,
    SourceBlock,
    SourceDocument,
)
from annotation.domain.textbook import BlueprintWindow, HierarchicalBlueprintTrace, TextbookStructure

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
    skip_reason: str | None = None


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
    evidence_notes: list[EvidenceNote] = Field(default_factory=list)


class RetrievalDecision(BaseModel):
    """Queries chosen by the content agent before learner-facing generation."""

    model_config = ConfigDict(extra="forbid")
    textbook_queries: list[str] = Field(default_factory=list, max_length=4)
    web_queries: list[str] = Field(default_factory=list, max_length=2)
    action: Literal["search", "write"]
    reason: str = ""

    @model_validator(mode="after")
    def validate_action(self):
        queries = [q for q in [*self.textbook_queries, *self.web_queries] if q.strip()]
        if self.action == "search" and not queries:
            raise ValueError("search requires at least one query")
        if self.action == "write" and queries:
            raise ValueError("write cannot execute searches")
        return self



class ContentTaskDraft(BaseModel):
    """One structured course-architect decision mapped to ContentTask."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    covered_objectives: dict[str, list[str]] = Field(min_length=1)
    quiz_count: int | None = Field(default=None, ge=0)
    depth_guidance: Literal["concise", "standard", "detailed"]
    source_refs: list[str] = Field(default_factory=list)
    interactive_component_policy: Literal["required", "skip"] = "skip"
    acceptance_criteria: list[str] = Field(default_factory=list)


class ContentTaskPlanDraft(BaseModel):
    """A-005 course architect output: the ContentTask list itself."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[ContentTaskDraft] = Field(default_factory=list)


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


class FactCheckRetrievalDecision(BaseModel):
    """Tool choice made by the fact-check agent for one assertion."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["use_context", "search_textbook", "search_web", "search_both"]
    textbook_queries: list[str] = Field(default_factory=list, max_length=3)
    web_queries: list[str] = Field(default_factory=list, max_length=2)
    reason: str = ""

    @model_validator(mode="after")
    def validate_queries(self) -> "FactCheckRetrievalDecision":
        if self.action in {"search_textbook", "search_both"} and not any(q.strip() for q in self.textbook_queries):
            raise ValueError("textbook search requires a query")
        if self.action in {"search_web", "search_both"} and not any(q.strip() for q in self.web_queries):
            raise ValueError("web search requires a query")
        return self


class QuizRetrievalDecision(BaseModel):
    """Evidence tool choice made by the exercise agent."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["use_context", "search"]
    textbook_queries: list[str] = Field(default_factory=list, max_length=3)
    reason: str = ""

    @model_validator(mode="after")
    def validate_queries(self) -> "QuizRetrievalDecision":
        if self.action == "search" and not any(q.strip() for q in self.textbook_queries):
            raise ValueError("search requires a query")
        if self.action == "use_context" and self.textbook_queries:
            raise ValueError("use_context cannot include queries")
        return self


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


class InteractiveComponentDraft(BaseModel):
    """A-004 model output: a data specification, never frontend source code."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    knowledge_unit_id: str = Field(min_length=1)
    context_pack_id: str = Field(min_length=1)
    spec: InteractiveComponentSpec | None = None
    not_needed_reason: str | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> "InteractiveComponentDraft":
        if self.spec is None and not (self.not_needed_reason or "").strip():
            raise ValueError("a component draft without a spec must explain why it is not needed")
        if self.spec is not None and self.not_needed_reason is not None:
            raise ValueError("a component draft cannot include both a spec and not_needed_reason")
        return self


class InteractiveComponentState(TypedDict, total=False):
    """State for one bounded component generation/validation subgraph."""

    run_id: str
    plan: InteractiveComponentPlan
    task: InteractiveComponentTask
    unit: KnowledgeUnit
    content_artifact: ContentArtifact
    context_pack: ContextPack
    valid_source_refs: list[str]
    max_attempts: int
    attempt: int
    attempts: list[InteractiveComponentAttemptTrace]
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
    candidate_draft: InteractiveComponentDraft | None
    candidate_artifact: InteractiveComponentArtifact | None
    hard_check: InteractiveComponentHardCheckResult | None
    sandbox_report: InteractiveComponentSandboxReport | None
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
    interactive_component_loop_trace: InteractiveComponentLoopTrace
    final_artifact: InteractiveComponentArtifact | None


class ContentReflectionState(TypedDict, total=False):
    """State for one bounded explanation/reflection subgraph invocation."""

    run_id: str
    task: ContentTask
    unit: KnowledgeUnit
    context_pack: ContextPack
    valid_source_refs: list[str]
    max_attempts: int
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
    source_blocks: list[SourceBlock]
    semantic_blocks: list[SourceBlock]
    blueprint: LearningBlueprint
    retrieval_decision: RetrievalDecision | None


class WorkflowState(TypedDict, total=False):
    run_id: str
    document_id: str
    pdf_path: str
    source_document: SourceDocument
    source_blocks: list[SourceBlock]
    semantic_blocks: list[SourceBlock]
    source_refs: list[str]
    textbook_structure: TextbookStructure
    blueprint_windows: list[BlueprintWindow]
    blueprint_mode: str
    blueprint_hierarchy_trace: HierarchicalBlueprintTrace
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
    content_blueprint: LearningBlueprint
    content_tasks: list[ContentTask]
    content_task_planning_metadata: dict[str, Any]
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
    interactive_component_plan: InteractiveComponentPlan
    interactive_component_results: list[dict[str, Any]]
    interactive_component_task: InteractiveComponentTask
    interactive_component_artifacts: list[InteractiveComponentArtifact]
    interactive_component_loop_trace: InteractiveComponentLoopTrace
    interactive_component_summary: dict[str, Any]
    interactive_component_status: str
    interactive_component_artifact_path: str
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


