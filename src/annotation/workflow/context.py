"""Configuration and ephemeral resources passed explicitly to workflow nodes."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable

from annotation.config import rag_chroma_path, rag_enabled
from annotation.domain.artifacts import FactCheckPolicy, SourceBlock
from annotation.fact_checking import (
    SQLiteFts5TextbookDatabaseSearchSkill,
    web_search_skill_from_env,
)
from annotation.providers import ModelProvider
from annotation.retrieval import (
    HybridTextbookSearchTool,
    LlamaIndexChromaTextbookSearchTool,
    UnifiedRetrievalService,
    index_source_blocks,
)
from annotation.retrieval.llamaindex_chroma import UnavailableVectorTextbookSearchTool
from annotation.workflow.concurrency import LockedSearch
from annotation.workflow.control import ControlledPort, RunControl

logger = logging.getLogger(__name__)


@dataclass
class RunResources:
    """One invocation owns its clients; state and checkpoints never contain them."""

    connection: sqlite3.Connection | None = field(default=None, init=False)
    retrieval_service: Any = field(default=None, init=False)
    web_search_skill: Any = field(default=None, init=False)
    warnings: list[str] = field(default_factory=list, init=False)

    def __enter__(self) -> RunResources:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
        self.connection = None
        self.retrieval_service = None
        self.web_search_skill = None

    def web(self, control: RunControl | None = None) -> Any:
        if self.web_search_skill is None:
            self.web_search_skill = web_search_skill_from_env()
        return (
            ControlledPort(self.web_search_skill, control)
            if control
            else self.web_search_skill
        )

    def prepare_index(self, semantic_blocks: list[SourceBlock]) -> None:
        if self.connection is None:
            self.connection = sqlite3.connect(":memory:", check_same_thread=False)
            try:
                index_source_blocks(self.connection, semantic_blocks)
            except BaseException:
                self.close()
                raise

    def retrieval(
        self, semantic_blocks: list[SourceBlock], control: RunControl | None = None
    ) -> Any:
        if self.retrieval_service is None:
            self.prepare_index(semantic_blocks)
            textbook = LockedSearch(
                SQLiteFts5TextbookDatabaseSearchSkill(self.connection)
            )
            if rag_enabled():
                try:
                    vector = LlamaIndexChromaTextbookSearchTool(
                        semantic_blocks,
                        persist_directory=rag_chroma_path(),
                    )
                except Exception as exc:
                    logger.exception("Vector index initialization failed")
                    vector = UnavailableVectorTextbookSearchTool(type(exc).__name__)
                    self.warnings.append(
                        f"vector_index_initialization_failed:{type(exc).__name__}"
                    )
                # Client locks are independent: web requests never acquire them.
                textbook = HybridTextbookSearchTool(textbook, LockedSearch(vector))
            service = UnifiedRetrievalService(textbook, self.web())
            self.retrieval_service = (
                ControlledPort(service, control) if control else service
            )
        return self.retrieval_service


@dataclass(frozen=True)
class WorkflowContext:
    provider: ModelProvider
    blueprint_max_attempts: int = 3
    content_max_attempts: int = 3
    workers: int = 1
    fact_check_policy: FactCheckPolicy = field(default_factory=FactCheckPolicy)
    additional_fact_retrieval: bool = True
    component_max_attempts: int = 3
    sandbox_runner: Callable[..., Any] | None = None
    run_control: RunControl | None = None
    resources: RunResources | None = None

    @property
    def run_resources(self) -> RunResources:
        if self.resources is None:
            raise RuntimeError("workflow node requires a RunResources context")
        return self.resources
