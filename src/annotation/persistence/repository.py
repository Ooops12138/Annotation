"""Local persistence for textbook runs, artifacts, and learner documents.

The application keeps the artifact payloads (JSON, Markdown, and PDFs) on the
filesystem.  This module stores the durable index that relates those payloads
to a textbook, a workflow run, and a versioned document.  It intentionally
uses the standard-library :mod:`sqlite3` module so the POC can run without a
database service.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from annotation.config import LEGACY_SOURCE_INDEX_PATH, STORAGE_DIR


DEFAULT_DATABASE_PATH = STORAGE_DIR / "annotation.sqlite3"
SCHEMA_VERSION = 1


class PersistenceError(RuntimeError):
    """Base error for durable-record conflicts and invalid references."""


class ImmutableArtifactError(PersistenceError):
    """Raised when an artifact identity is reused for different content."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _decode(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def project_document_status(document_status: str | None, review_status: str | None) -> str:
    """Return the durable document projection authorized by a review.

    The database is also used directly by compatibility/import paths, so the
    release invariant belongs here as well as in the application service:
    warning-only reviews are readable ``accepted`` previews, while only a
    passed review can produce ``published``.
    """

    if document_status == "blocked" or review_status == "blocked":
        return "blocked"
    if review_status == "passed":
        return "published"
    if review_status == "at_risk":
        return "accepted"
    if review_status == "needs_revision":
        return "needs_revision"
    if document_status == "published":
        return "accepted"
    return document_status or "draft"


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_value(database: str | Path | sqlite3.Connection) -> str | Path | sqlite3.Connection:
    if isinstance(database, sqlite3.Connection):
        return database
    if str(database) == ":memory:":
        return ":memory:"
    return Path(database)


def _connect(database: str | Path | sqlite3.Connection) -> tuple[sqlite3.Connection, bool]:
    value = _path_value(database)
    if isinstance(value, sqlite3.Connection):
        connection = value
        owned = False
    else:
        if value != ":memory:":
            Path(value).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(value), timeout=30, check_same_thread=False)
        owned = True
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    if owned and value != ":memory:":
        # WAL keeps read-only library requests from blocking a workflow write.
        connection.execute("PRAGMA journal_mode = WAL")
    return connection, owned


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS books (
    book_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    title TEXT,
    page_count INTEGER,
    block_count INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    book_id TEXT REFERENCES books(book_id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    config_version TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'workflow',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS runs_book_idx ON runs(book_id, started_at DESC);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    status TEXT NOT NULL DEFAULT 'draft',
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(run_id, kind, version)
);

CREATE INDEX IF NOT EXISTS artifacts_run_idx ON artifacts(run_id, kind, version);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    current_version_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS documents_book_idx ON documents(book_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS document_versions (
    document_version_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    run_id TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    document_status TEXT NOT NULL,
    review_status TEXT,
    artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(document_id, version)
);

CREATE INDEX IF NOT EXISTS document_versions_document_idx
    ON document_versions(document_id, version DESC);

CREATE TABLE IF NOT EXISTS source_blocks (
    source_ref TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    block_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    artifact_id TEXT,
    run_id TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'draft',
    source_refs_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL DEFAULT 'migration',
    raw_text TEXT NOT NULL DEFAULT '',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE VIRTUAL TABLE IF NOT EXISTS source_blocks_fts USING fts5(
    source_ref UNINDEXED,
    text
);
"""


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_compatible_source_schema(connection: sqlite3.Connection) -> None:
    """Add optional source-artifact fields to an older source index copy."""

    # ``source-index.sqlite3`` predates the unified database and only has the
    # first seven columns.  ALTER TABLE is safe and idempotent for an existing
    # annotation.sqlite3 created by an earlier development build.
    for column, definition in (
        ("artifact_id", "TEXT"),
        ("run_id", "TEXT"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("status", "TEXT NOT NULL DEFAULT 'draft'"),
        ("source_refs_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("created_by", "TEXT NOT NULL DEFAULT 'migration'"),
        ("raw_text", "TEXT NOT NULL DEFAULT ''"),
        ("warnings_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ):
        _ensure_column(connection, "source_blocks", column, definition)


def _rebuild_source_fts(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM source_blocks_fts")
    connection.execute(
        "INSERT INTO source_blocks_fts(rowid, source_ref, text) "
        "SELECT rowid, source_ref, text FROM source_blocks"
    )


def initialize_database(database: str | Path | sqlite3.Connection = DEFAULT_DATABASE_PATH) -> None:
    """Create or upgrade the local business database, safely and idempotently."""

    connection, owned = _connect(database)
    try:
        connection.executescript(SCHEMA_SQL)
        _ensure_compatible_source_schema(connection)
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()
    finally:
        if owned:
            connection.close()


class PersistenceRepository:
    """Transaction-safe repository backed by one SQLite connection."""

    def __init__(self, database: str | Path | sqlite3.Connection = DEFAULT_DATABASE_PATH) -> None:
        self._connection, self._owned = _connect(database)
        self._lock = threading.RLock()
        initialize_database(self._connection)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @property
    def database_path(self) -> str:
        value = self._connection.execute("PRAGMA database_list").fetchone()
        return str(value[2]) if value else ""

    def close(self) -> None:
        if self._owned:
            self._connection.close()
            self._owned = False

    def __enter__(self) -> "PersistenceRepository":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def _read_one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(query, params).fetchone()
        return _row_dict(row) if row else None

    def _read_all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [_row_dict(row) for row in rows]

    # Books -----------------------------------------------------------------
    def register_book(
        self,
        pdf_path: str | Path,
        *,
        book_id: str | None = None,
        original_filename: str | None = None,
        stored_path: str | Path | None = None,
        title: str | None = None,
        page_count: int | None = None,
        block_count: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        copy_to_storage: bool = True,
    ) -> dict[str, Any]:
        """Register a PDF by content hash and return the durable book row.

        Registering the same bytes repeatedly is idempotent.  The first stored
        path and descriptive metadata remain authoritative for that hash.
        """

        source = Path(pdf_path)
        if not source.is_file():
            raise FileNotFoundError(f"PDF not found: {source}")
        digest = _sha256_path(source)
        original_name = original_filename or source.name
        destination = Path(stored_path) if stored_path else STORAGE_DIR / "books" / f"{digest}.pdf"
        if copy_to_storage:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != destination.resolve():
                if not destination.exists() or _sha256_path(destination) != digest:
                    shutil.copyfile(source, destination)
        else:
            destination = source
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM books WHERE sha256 = ?", (digest,)).fetchone()
            if row:
                # A seed registration may happen before PDF metadata is
                # known. Enrich missing measurements on a later upload while
                # keeping the original filename/title authoritative.
                enrich: dict[str, Any] = {}
                if row["page_count"] is None and page_count is not None:
                    enrich["page_count"] = page_count
                if row["block_count"] is None and block_count is not None:
                    enrich["block_count"] = block_count
                if (row["title"] is None or not str(row["title"]).strip()) and title:
                    enrich["title"] = title
                if enrich:
                    enrich["updated_at"] = now
                    assignments = ", ".join(f"{key} = ?" for key in enrich)
                    connection.execute(
                        f"UPDATE books SET {assignments} WHERE book_id = ?",
                        (*enrich.values(), row["book_id"]),
                    )
                    row = connection.execute("SELECT * FROM books WHERE book_id = ?", (row["book_id"],)).fetchone()
                return _row_dict(row)
            identifier = book_id or f"book-{digest[:16]}"
            connection.execute(
                """
                INSERT INTO books
                    (book_id, sha256, original_filename, stored_path, title,
                     page_count, block_count, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (identifier, digest, original_name, str(destination.resolve()), title or source.stem,
                 page_count, block_count, _json(metadata), now, now),
            )
            return _row_dict(connection.execute("SELECT * FROM books WHERE book_id = ?", (identifier,)).fetchone())

    # Common aliases make the boundary convenient for API and import code.
    upsert_book = register_book
    get_or_create_book = register_book

    def get_book(self, book_id: str) -> dict[str, Any] | None:
        return self._read_one("SELECT * FROM books WHERE book_id = ?", (book_id,))

    def get_book_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        return self._read_one("SELECT * FROM books WHERE sha256 = ?", (sha256,))

    def list_books(self) -> list[dict[str, Any]]:
        return self._read_all("SELECT * FROM books ORDER BY updated_at DESC, book_id")

    # Runs ------------------------------------------------------------------
    def create_run(
        self,
        book_id: str | None = None,
        *,
        run_id: str | None = None,
        status: str = "running",
        provider: str = "",
        model: str = "",
        config_version: str = "",
        origin: str = "workflow",
        started_at: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        identifier = run_id or f"run-{uuid.uuid4().hex[:16]}"
        started = started_at or _utc_now()
        with self._transaction() as connection:
            existing = connection.execute("SELECT * FROM runs WHERE run_id = ?", (identifier,)).fetchone()
            if existing:
                return _row_dict(existing)
            if book_id is not None and not connection.execute("SELECT 1 FROM books WHERE book_id = ?", (book_id,)).fetchone():
                raise KeyError(f"Unknown book: {book_id}")
            connection.execute(
                """
                INSERT INTO runs
                    (run_id, book_id, status, provider, model, config_version,
                     origin, started_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (identifier, book_id, status, provider, model, config_version, origin, started, _json(metadata)),
            )
            return _row_dict(connection.execute("SELECT * FROM runs WHERE run_id = ?", (identifier,)).fetchone())

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        config_version: str | None = None,
        origin: str | None = None,
        finished_at: str | None = None,
        error: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        for key, value in (("status", status), ("provider", provider), ("model", model),
                           ("config_version", config_version), ("origin", origin),
                           ("finished_at", finished_at), ("error", error)):
            if value is not None:
                updates[key] = value
        if metadata is not None:
            updates["metadata_json"] = _json(metadata)
        if status in {"succeeded", "blocked", "failed", "cancelled"} and finished_at is None:
            updates["finished_at"] = _utc_now()
        if not updates:
            found = self.get_run(run_id)
            if not found:
                raise KeyError(f"Unknown run: {run_id}")
            return found
        with self._transaction() as connection:
            if not connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone():
                raise KeyError(f"Unknown run: {run_id}")
            assignments = ", ".join(f"{key} = ?" for key in updates)
            connection.execute(f"UPDATE runs SET {assignments} WHERE run_id = ?", (*updates.values(), run_id))
            return _row_dict(connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._read_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))

    def list_runs(self, book_id: str | None = None) -> list[dict[str, Any]]:
        if book_id is None:
            return self._read_all("SELECT * FROM runs ORDER BY started_at DESC, run_id")
        return self._read_all("SELECT * FROM runs WHERE book_id = ? ORDER BY started_at DESC, run_id", (book_id,))

    # Artifacts -------------------------------------------------------------
    def register_artifact(
        self,
        run_id: str,
        kind: str,
        path: str | Path,
        *,
        artifact_id: str | None = None,
        version: int = 1,
        status: str = "draft",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = Path(path)
        if not payload.is_file():
            raise FileNotFoundError(f"Artifact not found: {payload}")
        digest = _sha256_path(payload)
        identifier = artifact_id or f"artifact-{uuid.uuid4().hex[:16]}"
        created = _utc_now()
        with self._transaction() as connection:
            if not connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone():
                raise KeyError(f"Unknown run: {run_id}")
            existing = connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (identifier,)).fetchone()
            if existing:
                existing_dict = _row_dict(existing)
                if existing_dict["sha256"] != digest or existing_dict["path"] != str(payload.resolve()):
                    raise ImmutableArtifactError(f"Artifact {identifier} already points to different content")
                return existing_dict
            conflict = connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? AND kind = ? AND version = ?",
                (run_id, kind, version),
            ).fetchone()
            if conflict:
                raise ImmutableArtifactError(f"Artifact slot {run_id}/{kind}/v{version} is already registered")
            connection.execute(
                """
                INSERT INTO artifacts
                    (artifact_id, run_id, kind, version, status, path, sha256,
                     metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (identifier, run_id, kind, version, status, str(payload.resolve()), digest, _json(metadata), created),
            )
            return _row_dict(connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (identifier,)).fetchone())

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        return self._read_one("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,))

    def list_artifacts(self, run_id: str, *, kind: str | None = None) -> list[dict[str, Any]]:
        if kind is None:
            return self._read_all("SELECT * FROM artifacts WHERE run_id = ? ORDER BY kind, version", (run_id,))
        return self._read_all("SELECT * FROM artifacts WHERE run_id = ? AND kind = ? ORDER BY version", (run_id, kind))

    save_artifact = register_artifact

    # Documents and versions -----------------------------------------------
    def create_document(
        self,
        book_id: str,
        title: str,
        *,
        document_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        identifier = document_id or f"doc-{uuid.uuid4().hex[:16]}"
        now = _utc_now()
        with self._transaction() as connection:
            if not connection.execute("SELECT 1 FROM books WHERE book_id = ?", (book_id,)).fetchone():
                raise KeyError(f"Unknown book: {book_id}")
            existing = connection.execute("SELECT * FROM documents WHERE document_id = ?", (identifier,)).fetchone()
            if existing:
                return _row_dict(existing)
            connection.execute(
                """
                INSERT INTO documents
                    (document_id, book_id, title, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (identifier, book_id, title, _json(metadata), now, now),
            )
            return _row_dict(connection.execute("SELECT * FROM documents WHERE document_id = ?", (identifier,)).fetchone())

    def add_document_version(
        self,
        document_id: str,
        *,
        run_id: str | None = None,
        version: int | None = None,
        document_status: str = "published",
        review_status: str | None = None,
        artifact_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        document_version_id: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            if not connection.execute("SELECT 1 FROM documents WHERE document_id = ?", (document_id,)).fetchone():
                raise KeyError(f"Unknown document: {document_id}")
            selected_version = version
            if selected_version is None:
                row = connection.execute("SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM document_versions WHERE document_id = ?", (document_id,)).fetchone()
                selected_version = int(row["next_version"])
            identifier = document_version_id or f"{document_id}-v{selected_version}"
            existing = connection.execute("SELECT * FROM document_versions WHERE document_version_id = ?", (identifier,)).fetchone()
            if existing:
                return _row_dict(existing)
            conflict = connection.execute("SELECT * FROM document_versions WHERE document_id = ? AND version = ?", (document_id, selected_version)).fetchone()
            if conflict:
                raise PersistenceError(f"Document version {document_id}/v{selected_version} already exists")
            if run_id is not None and not connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone():
                raise KeyError(f"Unknown run: {run_id}")
            if artifact_id is not None and not connection.execute("SELECT 1 FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone():
                raise KeyError(f"Unknown artifact: {artifact_id}")
            document_status = project_document_status(document_status, review_status)
            created = _utc_now()
            connection.execute(
                """
                INSERT INTO document_versions
                    (document_version_id, document_id, run_id, version,
                     document_status, review_status, artifact_id,
                     metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (identifier, document_id, run_id, selected_version, document_status, review_status,
                 artifact_id, _json(metadata), created),
            )
            current = connection.execute(
                "SELECT current_version_id FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            # Keep a previously usable version current when a later run is
            # blocked. The blocked version remains indexed for audit, and a
            # first-ever blocked version is still queryable for diagnostics.
            if document_status != "blocked" or not current or not current["current_version_id"]:
                connection.execute(
                    "UPDATE documents SET current_version_id = ?, updated_at = ? WHERE document_id = ?",
                    (identifier, created, document_id),
                )
            return _row_dict(connection.execute("SELECT * FROM document_versions WHERE document_version_id = ?", (identifier,)).fetchone())

    create_document_version = add_document_version
    save_document_version = add_document_version

    def get_document(self, document_id: str, *, include_versions: bool = False) -> dict[str, Any] | None:
        document = self._read_one("SELECT * FROM documents WHERE document_id = ?", (document_id,))
        if document and include_versions:
            document["versions"] = self.list_document_versions(document_id)
        return document

    def list_documents(self, book_id: str | None = None) -> list[dict[str, Any]]:
        if book_id is None:
            return self._read_all("SELECT * FROM documents ORDER BY updated_at DESC, document_id")
        return self._read_all("SELECT * FROM documents WHERE book_id = ? ORDER BY updated_at DESC, document_id", (book_id,))

    def get_document_version(self, document_version_id: str) -> dict[str, Any] | None:
        return self._read_one("SELECT * FROM document_versions WHERE document_version_id = ?", (document_version_id,))

    def get_current_document_version(self, document_id: str) -> dict[str, Any] | None:
        return self._read_one(
            """
            SELECT dv.* FROM document_versions dv
            JOIN documents d ON d.current_version_id = dv.document_version_id
            WHERE d.document_id = ?
            """,
            (document_id,),
        )

    def list_document_versions(self, document_id: str) -> list[dict[str, Any]]:
        return self._read_all("SELECT * FROM document_versions WHERE document_id = ? ORDER BY version DESC", (document_id,))

    # Library and source-index migration -----------------------------------
    def list_library(self) -> list[dict[str, Any]]:
        """Return books with their saved documents and current versions."""

        books = self.list_books()
        for book in books:
            documents = self.list_documents(book["book_id"])
            for document in documents:
                document["current_version"] = self.get_current_document_version(document["document_id"])
            book["documents"] = documents
        return books

    get_library = list_library
    library = list_library

    def migrate_source_index(self, source_database: str | Path = LEGACY_SOURCE_INDEX_PATH) -> int:
        """Copy legacy ``source_blocks`` rows and rebuild FTS in this database.

        The source database is opened read-only from the repository's point of
        view.  Re-running this method upserts the same source references and
        never duplicates them.
        """

        source_path = Path(source_database)
        if not source_path.is_file():
            return 0
        if self.database_path and Path(self.database_path).resolve() == source_path.resolve():
            return 0
        source, owned = _connect(source_path)
        try:
            try:
                rows = source.execute(
                    "SELECT source_ref, document_id, page_number, block_index, text, text_hash, parser_version FROM source_blocks"
                ).fetchall()
            except sqlite3.OperationalError:
                # A configured legacy path may be an empty SQLite file.  It is
                # still a valid no-op migration rather than a startup failure.
                rows = []
        finally:
            if owned:
                source.close()
        with self._transaction() as connection:
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO source_blocks
                        (source_ref, document_id, page_number, block_index, text,
                         text_hash, parser_version, artifact_id, source_refs_json,
                         raw_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_ref) DO UPDATE SET
                        document_id=excluded.document_id,
                        page_number=excluded.page_number,
                        block_index=excluded.block_index,
                        text=excluded.text,
                        text_hash=excluded.text_hash,
                        parser_version=excluded.parser_version,
                        raw_text=excluded.raw_text
                    """,
                    (row["source_ref"], row["document_id"], row["page_number"], row["block_index"],
                     row["text"], row["text_hash"], row["parser_version"],
                     f"{row['source_ref']}-artifact", _json([row["source_ref"]]), row["text"]),
                )
            _rebuild_source_fts(connection)
        return len(rows)


Repository = PersistenceRepository


def migrate_source_index(
    source_database: str | Path = LEGACY_SOURCE_INDEX_PATH,
    database: str | Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Convenience wrapper for one-shot startup/CLI migrations."""

    with PersistenceRepository(database) as repository:
        return repository.migrate_source_index(source_database)


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in tuple(result):
        if key.endswith("_json"):
            result[key[:-5]] = _decode(result.pop(key), {} if key.endswith("metadata_json") else [])
    return result


__all__ = [
    "DEFAULT_DATABASE_PATH",
    "ImmutableArtifactError",
    "PersistenceError",
    "PersistenceRepository",
    "Repository",
    "initialize_database",
    "migrate_source_index",
]
