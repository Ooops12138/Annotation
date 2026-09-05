from typing import Annotated, Literal, Union

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


class SourceBlock(ArtifactBase):
    source_ref: str
    document_id: str
    page_number: int = Field(ge=1)
    block_index: int = Field(ge=0)
    text: str
    text_hash: str
    parser_version: str
    bbox: tuple[float, float, float, float]


class KnowledgeUnit(ArtifactBase):
    title: str
    kind: Literal["concept", "formula", "theorem", "example", "skill"]
    learning_objectives: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)


class LearningBlueprint(ArtifactBase):
    title: str
    knowledge_units: list[KnowledgeUnit] = Field(default_factory=list)


class ContentArtifact(ArtifactBase):
    content_type: Literal["explanation", "example", "quiz", "digital"]
    knowledge_unit_ids: list[str] = Field(default_factory=list)
    content: str


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str
    category: Literal["fact", "logic", "coverage", "source", "uncertainty"]
    severity: Literal["info", "warning", "blocking"]
    message: str
    target_id: str | None = None


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


DocumentNode = Annotated[Union[MarkdownNode, FormulaNode, CalloutNode, QuizNode], Field(discriminator="type")]


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


class RunMetadata(BaseModel):
    run_id: str
    status: str
    provider: str
    model: str
    config_version: str


ArtifactBase.model_rebuild()
