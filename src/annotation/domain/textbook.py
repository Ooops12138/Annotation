"""Textbook locations and execution records; these are not a knowledge inventory."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from annotation.prompt_archive import PromptReference


class SourceRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int = Field(ge=0)
    end: int = Field(ge=0)  # exclusive ordinal in ordered_source_refs


class StructureNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str
    parent_id: str | None = None
    title: str
    level: int = Field(ge=0)
    kind: Literal["book", "chapter", "section"]
    origin: Literal["document", "bookmark", "heading"]
    start: int
    end: int
    owned_ranges: list[SourceRange] = Field(default_factory=list)


class TextbookStructure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["textbook-structure-v1"] = "textbook-structure-v1"
    document_id: str
    ordered_source_refs: list[str]
    nodes: list[StructureNode]
    warnings: list[str] = Field(default_factory=list)


class SourceSlice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ref: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)  # exclusive character offsets, never rewritten text


class BlueprintWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_id: str
    node_id: str
    path: list[str]
    slices: list[SourceSlice]
    context_slices: list[SourceSlice] = Field(default_factory=list)


class BlueprintCallTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: str
    scope_id: str
    attempt: int
    prompt: str | PromptReference
    system_prompt: str | PromptReference = ""
    raw_output: str = ""
    parsed_output: dict[str, Any] | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    estimated_input_tokens: int = 0
    input_budget_tokens: int = 0
    output_budget_tokens: int = 0
    cache_key: str = ""
    cache_hit: bool = False
    model_called: bool = False
    cached_prompt: str | PromptReference | None = None
    error: str | None = None
    error_category: str | None = None
    route: Literal["accept", "revise", "block", "fail"] = "accept"


class WindowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: BlueprintWindow
    status: Literal["pending", "accepted", "skipped", "blocked", "failed"] = "pending"
    unit_ids: list[str] = Field(default_factory=list)
    skip_reason: str | None = None


class HierarchicalBlueprintTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["blueprint-hierarchy-v1"] = "blueprint-hierarchy-v1"
    windows: list[WindowResult] = Field(default_factory=list)
    calls: list[BlueprintCallTrace] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    groups: list[dict[str, Any]] = Field(default_factory=list)
    merges: dict[str, str] = Field(default_factory=dict)
    final_status: Literal["accepted", "blocked", "failed"] = "blocked"
    stop_reason: str = "not_started"

