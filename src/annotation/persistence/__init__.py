"""Durable SQLite repository for textbook runs and generated documents."""

from .repository import (
    DEFAULT_DATABASE_PATH,
    ImmutableArtifactError,
    PersistenceError,
    PersistenceRepository,
    Repository,
    initialize_database,
    migrate_source_index,
    project_document_status,
)

__all__ = [
    "DEFAULT_DATABASE_PATH",
    "ImmutableArtifactError",
    "PersistenceError",
    "PersistenceRepository",
    "Repository",
    "initialize_database",
    "migrate_source_index",
    "project_document_status",
]
