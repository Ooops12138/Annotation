from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class ArtifactBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    run_id: str
    version: int = Field(ge=1)
    status: Literal["draft", "checking", "accepted", "needs_revision", "blocked", "published", "fixture"]
    source_refs: list[str] = Field(default_factory=list)
    created_by: str
    issues: list["ReviewIssue"] = Field(default_factory=list)


class SourceDocument(ArtifactBase):
    title: str
    locator: str
    warnings: list[str] = Field(default_factory=list)
    run_metadata: dict[str, Any] = Field(default_factory=dict)


class OCRCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    source_ref: str
    bbox: tuple[float, float, float, float]
    status: str
    recognizer: str
    text: str | None = None
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)
    image_path: str | None = None


class FormulaCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    source_ref: str
    bbox: tuple[float, float, float, float]
    status: str
    recognizer: str
    latex: str | None = None
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)
    image_path: str | None = None


class SourceBlock(ArtifactBase):
    source_ref: str
    document_id: str
    page_number: int = Field(ge=1)
    block_index: int = Field(ge=0)
    text: str
    text_hash: str
    parser_version: str
    bbox: tuple[float, float, float, float]
    raw_text: str = ""
    ocr_candidates: list[OCRCandidate] = Field(default_factory=list)
    formula_latex_candidates: list[FormulaCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    run_metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeUnit(ArtifactBase):
    title: str
    kind: Literal["concept", "formula", "theorem", "example", "skill"]
    learning_objectives: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    related_unit_ids: list[str] = Field(default_factory=list)
    teaching_materials: list[str] = Field(default_factory=list)


class LearningBlueprint(ArtifactBase):
    title: str
    knowledge_units: list[KnowledgeUnit] = Field(default_factory=list)


class BlueprintCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["accepted", "needs_revision", "blocked"]
    issues: list["ReviewIssue"] = Field(default_factory=list)
    covered_kinds: list[str] = Field(default_factory=list)
    checked_unit_ids: list[str] = Field(default_factory=list)


class ContentArtifact(ArtifactBase):
    content_type: Literal["explanation", "example", "quiz", "digital"]
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    title: str | None = None
    material_role: Literal["explanation", "example", "proof", "bridge", "supplement"] = "explanation"
    task_id: str | None = None
    context_pack_id: str | None = None
    prompt_version: str | None = None
    parent_artifact_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    content: str


class ContentTask(BaseModel):
    """Explicit unit of work for T-006 content generation.

    A task is deliberately smaller than a chapter.  It identifies the one
    knowledge unit being generated and the evidence/acceptance conditions that
    the orchestrator must provide to a content agent.
    """

    model_config = ConfigDict(extra="forbid")
    task_id: str
    run_id: str
    blueprint_version: str
    knowledge_unit_id: str
    content_types: list[Literal["explanation", "example", "proof", "bridge", "teaching_material"]] = Field(default_factory=lambda: ["explanation", "teaching_material"])
    source_refs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: Literal["planned", "generating", "accepted", "needs_revision", "blocked"] = "planned"


class ContextExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ref: str
    page_number: int = Field(ge=1)
    block_index: int = Field(ge=0)
    text: str


class ContextPack(BaseModel):
    """The bounded, auditable input assembled for one model call."""

    model_config = ConfigDict(extra="forbid")
    context_pack_id: str
    run_id: str
    task_id: str
    knowledge_unit_id: str
    source_refs: list[str] = Field(default_factory=list)
    selected_source_refs: list[str] = Field(default_factory=list)
    excerpts: list[ContextExcerpt] = Field(default_factory=list)
    prerequisite_titles: list[str] = Field(default_factory=list)
    estimated_input_tokens: int = Field(ge=0)
    input_budget_tokens: int = Field(ge=0)
    omitted_source_refs: list[str] = Field(default_factory=list)
    retrieval_strategy: list[str] = Field(default_factory=list)
    source_snapshot: str | None = None


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str
    category: Literal["fact", "logic", "formula", "coverage", "source", "transition", "conflict", "uncertainty", "stance"]
    severity: Literal["info", "warning", "blocking"]
    message: str
    target_id: str | None = None
    # `layer` keeps the review meaning separate from the severity.  In the
    # first phase this lets the UI distinguish fact-risk annotations from
    # structural checks without pretending that an automated review has
    # reached a final verdict.
    layer: Literal["fact", "stance", "structure"] = "structure"
    source_refs: list[str] = Field(default_factory=list)
    suggested_action: str | None = None


class ReviewReport(BaseModel):
    """Versioned, auditable result of the basic document review stage."""

    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    report_id: str
    run_id: str
    version: int = Field(ge=1)
    status: Literal["passed", "at_risk", "blocked", "needs_revision"]
    created_by: str
    document_id: str
    reviewed_artifact_id: str
    source_refs: list[str] = Field(default_factory=list)
    issues: list[ReviewIssue] = Field(default_factory=list)
    issue_counts: dict[str, int] = Field(default_factory=dict)
    checks: dict[str, str] = Field(default_factory=dict)
    human_review_required: bool = False
    revision_of: str | None = None
    recommendations: list[str] = Field(default_factory=list)
    artifact_path: str | None = None


class MarkdownNode(BaseModel):
    type: Literal["markdown"] = "markdown"
    id: str
    content: str
    source_refs: list[str] = Field(default_factory=list)


class FormulaNode(BaseModel):
    type: Literal["formula"] = "formula"
    id: str
    latex: str
    source_refs: list[str] = Field(default_factory=list)


class CalloutNode(BaseModel):
    type: Literal["callout"] = "callout"
    id: str
    tone: Literal["info", "warning", "success"] = "info"
    title: str
    content: str


class QuizNode(BaseModel):
    type: Literal["quiz"] = "quiz"
    id: str
    question: str
    options: list[str]
    answer: str
    explanation: str
    source_refs: list[str] = Field(default_factory=list)


class ExampleNode(BaseModel):
    type: Literal["example"] = "example"
    id: str
    title: str
    problem: str
    solution: str
    source_refs: list[str] = Field(default_factory=list)


DocumentNode = Annotated[Union[MarkdownNode, FormulaNode, CalloutNode, QuizNode, ExampleNode], Field(discriminator="type")]


class DocumentSection(BaseModel):
    type: Literal["section"] = "section"
    id: str
    title: str
    children: list[DocumentNode] = Field(default_factory=list)


class LearningDocument(ArtifactBase):
    document_id: str
    blueprint_version: str
    title: str
    sections: list[DocumentSection] = Field(default_factory=list)
    review_report_id: str | None = None


class RunMetadata(BaseModel):
    run_id: str
    status: str
    provider: str
    model: str
    config_version: str


ArtifactBase.model_rebuild()
BlueprintCheckResult.model_rebuild()
