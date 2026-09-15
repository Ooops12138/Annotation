from typing import Annotated, Any, Literal, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


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
    """One readable Markdown artifact for a knowledge unit."""

    content_type: Literal["explanation"] = "explanation"
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    title: str | None = None
    task_id: str | None = None
    context_pack_id: str | None = None
    prompt_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    content: str


class QuizQuestion(BaseModel):
    """One learner-facing single-choice item inside a quiz artifact.

    The question keeps its own target and evidence references so a coverage
    report can audit an individual item without parsing generated prose.
    """

    model_config = ConfigDict(extra="forbid")
    question_id: str
    knowledge_unit_id: str
    target_objectives: list[str] = Field(default_factory=list)
    question: str
    options: list[str] = Field(default_factory=list)
    answer: str
    explanation: str
    source_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_choice_contract(self) -> "QuizQuestion":
        # Keep option identity exact: the renderer compares the selected value
        # and answer by string equality, so validation must not accept a
        # whitespace-normalized answer that is absent from the persisted list.
        options = list(self.options)
        if len(options) < 2:
            raise ValueError("quiz question requires at least two options")
        if any(not option.strip() for option in options):
            raise ValueError("quiz options must not be empty")
        if len(set(options)) != len(options):
            raise ValueError("quiz options must be unique")
        if self.answer not in options:
            raise ValueError("quiz answer must be one of the options")
        if options.count(self.answer) != 1:
            raise ValueError("quiz answer must identify exactly one option")
        if not self.question.strip():
            raise ValueError("quiz question must not be empty")
        if not self.explanation.strip():
            raise ValueError("quiz explanation must not be empty")
        return self


class QuizDraft(BaseModel):
    """Structured model response for one knowledge unit's quiz call."""

    model_config = ConfigDict(extra="forbid")
    knowledge_unit_id: str
    # The agent may choose any non-negative number, including zero.  Keeping
    # this optional preserves compatibility with older providers that only
    # returned the questions array; generation infers the count in that case.
    question_count: int | None = Field(default=None, ge=0)
    target_objectives: list[str] = Field(default_factory=list)
    questions: list[QuizQuestion] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_declared_count(self) -> "QuizDraft":
        if self.question_count is not None and self.question_count != len(self.questions):
            raise ValueError("quiz question_count must equal the number of questions")
        return self


class QuizArtifact(ArtifactBase):
    """Versioned, independently persisted quiz artifact for one unit."""

    content_type: Literal["quiz"] = "quiz"
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    target_objectives: list[str] = Field(default_factory=list)
    questions: list[QuizQuestion] = Field(default_factory=list)
    # This is the count selected by the quiz agent (or inferred from its
    # response), not a release minimum.  ``None`` keeps legacy artifacts
    # readable without fabricating a count.
    question_count: int | None = Field(default=None, ge=0)
    task_id: str | None = None
    context_pack_id: str | None = None
    prompt_version: str | None = None
    generation_metadata: dict[str, Any] = Field(default_factory=dict)
    raw_response: str = ""
    generation_error: str | None = None

    @model_validator(mode="after")
    def validate_recorded_count(self) -> "QuizArtifact":
        if self.question_count is not None and self.question_count != len(self.questions):
            raise ValueError("quiz question_count must equal the number of questions")
        return self

    @property
    def metadata(self) -> dict[str, Any]:
        """Compatibility view shared with the prose ContentArtifact contract."""

        return self.generation_metadata

    @property
    def raw_output(self) -> str:
        return self.raw_response

    @property
    def error(self) -> str | None:
        return self.generation_error


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
    content_types: list[Literal["explanation", "quiz"]] = Field(
        default_factory=lambda: ["explanation", "quiz"]
    )
    # A quiz task is one structured call per unit.  ``None`` means the quiz
    # agent chooses the number of questions, including zero.  A supplied value
    # is only a hint for deterministic fixtures and compatibility callers.
    quiz_count: int | None = Field(default=None, ge=0, validation_alias=AliasChoices("quiz_count", "quiz_question_count", "question_count"))
    source_refs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: Literal["planned", "generating", "accepted", "needs_revision", "blocked"] = "planned"

    @property
    def quiz_question_count(self) -> int | None:
        return self.quiz_count

    @model_validator(mode="after")
    def validate_content_types(self) -> "ContentTask":
        if self.content_types != ["explanation", "quiz"]:
            raise ValueError(
                "content_types must be exactly ['explanation', 'quiz']"
            )
        return self


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


class FactCheckPolicy(BaseModel):
    """Bounded policy recorded with every post-generation fact-check run."""

    model_config = ConfigDict(extra="forbid")

    max_claims_per_unit: int = Field(default=20, ge=1, le=50)
    max_corrections_per_unit: int = Field(default=2, ge=0, le=2)
    textbook_result_limit: int = Field(default=5, ge=1, le=10)
    web_enabled: bool = False
    web_query_limit: int = Field(default=10, ge=0, le=20)


class FactCheckClaim(BaseModel):
    """One auditable assertion extracted from an accepted content artifact."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    artifact_id: str
    knowledge_unit_id: str
    target_id: str
    role: Literal["explanation", "quiz_question", "quiz_answer", "quiz_explanation"]
    kind: Literal["fact", "stance"]
    text: str = Field(min_length=1)
    query: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)


class FactCheckEvidence(BaseModel):
    """A bounded textbook or external evidence excerpt retained in the trace."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_kind: Literal["textbook", "web"]
    provider: str
    locator: str
    text: str = Field(min_length=1)
    source_ref: str | None = None
    url: str | None = None
    rank: float | None = None


class FactCheckAssessment(BaseModel):
    """The policy decision for one claim, separate from learner-facing issues."""

    model_config = ConfigDict(extra="forbid")

    assessment_id: str
    claim_id: str
    verdict: Literal["supported", "contradicted", "insufficient", "external_conflict", "stance", "error"]
    judgement: str = Field(min_length=1)
    route: Literal["accept", "annotate", "revise_content", "regenerate_quiz", "exclude_quiz"]
    textbook_evidence: list[FactCheckEvidence] = Field(default_factory=list)
    external_evidence: list[FactCheckEvidence] = Field(default_factory=list)
    stop_reason: str | None = None


class FactCheckRoundTrace(BaseModel):
    """One check/correction pass for one knowledge unit."""

    model_config = ConfigDict(extra="forbid")

    round: int = Field(ge=0, le=2)
    content_artifact_id: str
    quiz_artifact_id: str | None = None
    claims: list[FactCheckClaim] = Field(default_factory=list)
    assessments: list[FactCheckAssessment] = Field(default_factory=list)
    route: Literal["accept", "annotate", "revise_content", "regenerate_quiz", "exclude_quiz"]
    correction_artifact_id: str | None = None
    stop_reason: str | None = None


class FactCheckUnitTrace(BaseModel):
    """Versioned A-003 trace for one knowledge unit after quiz generation."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    run_id: str
    task_id: str
    knowledge_unit_id: str
    max_corrections: int = Field(ge=0, le=2)
    rounds: list[FactCheckRoundTrace] = Field(default_factory=list)
    final_status: Literal["accepted", "at_risk", "failed"]
    corrections_used: int = Field(ge=0, le=2)
    stop_reason: str
    final_content_artifact_id: str | None = None
    final_quiz_artifact_id: str | None = None


class FactCheckArtifact(BaseModel):
    """Run-level A-003 evidence artifact persisted independently of content."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    run_id: str
    version: int = Field(ge=1)
    status: Literal["accepted", "at_risk", "failed"]
    created_by: str
    policy: FactCheckPolicy
    unit_traces: list[FactCheckUnitTrace] = Field(default_factory=list)
    issues: list[ReviewIssue] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    artifact_path: str | None = None


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


class ContentHardCheckResult(BaseModel):
    """Deterministic source, binding, and formula checks for one candidate."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "needs_revision", "blocked"]
    issues: list[ReviewIssue] = Field(default_factory=list)
    # The artifact itself remains in the attempt trace.  Keeping the result as
    # an ID reference avoids silently duplicating a candidate in every check.
    checked_artifact_id: str | None = None


class ContentCriticIssueDraft(BaseModel):
    """One pedagogical finding emitted by the content-quality Critic.

    Severity and loop routing intentionally do not appear in this model: the
    orchestrator maps these bounded codes to its deterministic policy.
    """

    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "objective_missing",
        "prerequisite_unexplained",
        "beginner_clarity",
        "organization",
        "wording",
    ]
    message: str = Field(min_length=1)
    suggested_action: str | None = None


class ContentCritiqueDraft(BaseModel):
    """Structured, teaching-only Critic output for one content candidate."""

    model_config = ConfigDict(extra="forbid")

    issues: list[ContentCriticIssueDraft] = Field(default_factory=list)


class ContentAttemptTrace(BaseModel):
    """Auditable record for one initial-generation or revision attempt."""

    model_config = ConfigDict(extra="forbid")

    # Attempt zero is reserved for preflight blocks such as a missing
    # ContextPack, where no model call is allowed.
    attempt: int = Field(ge=0)
    # The short names mirror BlueprintAttemptTrace and keep generic trace
    # consumers simple; stage-specific fields below retain full provenance.
    prompt: str = ""
    raw_output: str = ""
    parsed_output: dict[str, Any] | None = None
    generation_stage: Literal["initial", "revision", "skipped"] = "initial"
    generation_prompt: str = ""
    revision_prompt: str | None = None
    generation_raw_output: str = ""
    generation_parsed_output: dict[str, Any] | None = None
    # Each attempt produces one readable Markdown candidate. The complete
    # generation response remains available above for audit and retry.
    candidate_artifact: ContentArtifact | dict[str, Any] | None = None
    generation_status: Literal["succeeded", "schema_error", "provider_error", "skipped"] = "skipped"
    generation_error: str | None = None
    generation_error_category: str | None = None
    generation_provider_metadata: dict[str, Any] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    error_category: str | None = None
    hard_check: ContentHardCheckResult | None = None
    check: ContentHardCheckResult | None = None
    critic_prompt: str | None = None
    critic_raw_output: str = ""
    critic_parsed_output: dict[str, Any] | None = None
    critic_status: Literal["succeeded", "schema_error", "provider_error", "skipped"] = "skipped"
    critic_error: str | None = None
    critic_error_category: str | None = None
    critic_provider_metadata: dict[str, Any] = Field(default_factory=dict)
    feedback: list[ReviewIssue] = Field(default_factory=list)
    route: Literal["accept", "revise", "block", "fail"]
    stop_reason: Literal[
        "accepted",
        "max_attempts",
        "attempts_exhausted",
        "non_retryable_provider_error",
        "context_pack_missing",
        "context_pack_source_over_budget",
        "upstream_failure",
    ] | None = None

    @model_validator(mode="after")
    def synchronize_compatibility_fields(self) -> "ContentAttemptTrace":
        """Populate generic trace aliases without mutating stage-specific data."""

        if not self.prompt:
            self.prompt = self.revision_prompt or self.generation_prompt
        if not self.raw_output:
            self.raw_output = self.generation_raw_output
        if self.parsed_output is None:
            self.parsed_output = self.generation_parsed_output
        if not self.provider_metadata:
            self.provider_metadata = dict(self.generation_provider_metadata)
        if self.error is None:
            self.error = self.generation_error or self.critic_error
        if self.error_category is None:
            self.error_category = self.generation_error_category or self.critic_error_category
        if self.check is None:
            self.check = self.hard_check
        return self


class ContentUnitLoopTrace(BaseModel):
    """Versioned trace for one knowledge unit's content reflection loop."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    run_id: str
    task_id: str
    knowledge_unit_id: str
    # ``None`` records an explicit preflight failure before a ContextPack was
    # available; completed attempts must point at the pack used by the call.
    context_pack_id: str | None = None
    max_attempts: int = Field(ge=1, le=3)
    attempts: list[ContentAttemptTrace] = Field(default_factory=list)
    final_status: Literal["accepted", "blocked", "failed", "skipped"]
    final_attempt: int = Field(ge=0)
    stop_reason: Literal[
        "accepted",
        "max_attempts",
        "attempts_exhausted",
        "non_retryable_provider_error",
        "context_pack_missing",
        "context_pack_source_over_budget",
        "upstream_failure",
    ]
    final_content_artifact_id: str | None = None
    artifact_path: str | None = None


class BlueprintAttemptTrace(BaseModel):
    """Auditable record for one Blueprint generation/check attempt."""

    model_config = ConfigDict(extra="forbid")

    attempt: int = Field(ge=1)
    prompt: str
    raw_output: str = ""
    parsed_output: dict[str, Any] | None = None
    generation_status: Literal["succeeded", "schema_error", "provider_error"]
    error: str | None = None
    error_category: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    check: BlueprintCheckResult | None = None
    feedback: list[ReviewIssue] = Field(default_factory=list)
    route: Literal["accept", "revise", "block", "fail"]
    stop_reason: Literal["accepted", "max_attempts", "non_retryable_provider_error", "preloaded"] | None = None


class BlueprintLoopTrace(BaseModel):
    """Versioned, auditable trace for the complete Blueprint revision loop."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    run_id: str
    max_attempts: int = Field(ge=1)
    attempts: list[BlueprintAttemptTrace] = Field(default_factory=list)
    final_status: Literal["accepted", "blocked", "failed"]
    final_attempt: int = Field(ge=0)
    stop_reason: Literal["accepted", "max_attempts", "non_retryable_provider_error", "preloaded"]
    final_blueprint_artifact_id: str | None = None
    artifact_path: str | None = None


class MarkdownNode(BaseModel):
    type: Literal["markdown"] = "markdown"
    id: str
    content: str
    source_refs: list[str] = Field(default_factory=list)


class CalloutNode(BaseModel):
    type: Literal["callout"] = "callout"
    id: str
    tone: Literal["info", "warning", "success"] = "info"
    title: str
    content: str
    source_refs: list[str] = Field(default_factory=list)


class QuizNode(BaseModel):
    type: Literal["quiz"] = "quiz"
    id: str
    question: str
    options: list[str]
    answer: str
    explanation: str
    source_refs: list[str] = Field(default_factory=list)


DocumentNode = Annotated[Union[MarkdownNode, CalloutNode, QuizNode], Field(discriminator="type")]


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
BlueprintAttemptTrace.model_rebuild()
BlueprintLoopTrace.model_rebuild()

# Naming aliases keep item/set terminology explicit at call sites without
# creating duplicate Pydantic contracts.
QuizItem = QuizQuestion
QuizSetArtifact = QuizArtifact
QuizArtifactDraft = QuizDraft
