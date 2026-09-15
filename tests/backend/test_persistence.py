from __future__ import annotations

import sqlite3

import pytest

from annotation.persistence import ImmutableArtifactError, PersistenceRepository, initialize_database


def test_database_initialization_is_idempotent(tmp_path) -> None:
    database = tmp_path / "annotation.sqlite3"
    initialize_database(database)
    initialize_database(database)

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"schema_meta", "books", "runs", "artifacts", "documents", "document_versions"} <= tables
        assert connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0] == "1"


def test_book_hash_dedup_and_document_version_history(tmp_path) -> None:
    pdf = tmp_path / "chapter.pdf"
    pdf.write_bytes(b"stable textbook bytes")
    repository = PersistenceRepository(tmp_path / "annotation.sqlite3")
    first = repository.register_book(pdf)
    second = repository.register_book(pdf, original_filename="renamed.pdf")
    assert first["book_id"] == second["book_id"]
    assert len(repository.list_books()) == 1

    run = repository.create_run(first["book_id"], run_id="run-1", provider="mock", model="fixture")
    assert repository.update_run("run-1", status="failed", error="provider timeout")["finished_at"]
    document = repository.create_document(first["book_id"], "Chapter")
    version_one = repository.add_document_version(document["document_id"], run_id=run["run_id"], document_status="published", review_status="passed")
    version_two = repository.add_document_version(document["document_id"], run_id=run["run_id"], document_status="published", review_status="at_risk")
    assert version_one["version"] == 1
    assert version_two["version"] == 2
    assert version_one["document_status"] == "published"
    assert version_two["document_status"] == "accepted"
    assert version_two["review_status"] == "at_risk"
    assert repository.get_document(document["document_id"])["current_version_id"] == version_two["document_version_id"]
    assert len(repository.list_document_versions(document["document_id"])) == 2
    repository.close()


def test_artifact_registration_is_immutable_and_idempotent(tmp_path) -> None:
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"book")
    artifact = tmp_path / "content.json"
    artifact.write_text("{\"ok\": true}", encoding="utf-8")
    repository = PersistenceRepository(tmp_path / "annotation.sqlite3")
    book = repository.register_book(pdf)
    repository.create_run(book["book_id"], run_id="run-artifact")
    first = repository.register_artifact("run-artifact", "content", artifact, artifact_id="content-1")
    assert repository.register_artifact("run-artifact", "content", artifact, artifact_id="content-1")["sha256"] == first["sha256"]

    artifact.write_text("{\"ok\": false}", encoding="utf-8")
    with pytest.raises(ImmutableArtifactError):
        repository.register_artifact("run-artifact", "content", artifact, artifact_id="content-1")
    repository.close()


def test_legacy_source_index_migration_rebuilds_fts_idempotently(tmp_path) -> None:
    legacy = tmp_path / "source-index.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.executescript(
            """
            CREATE TABLE source_blocks (
                source_ref TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                block_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                parser_version TEXT NOT NULL
            );
            INSERT INTO source_blocks VALUES
                ('src-p1-b0', 'doc-1', 1, 0, 'complete ordered field', 'hash-1', 'parser-v1'),
                ('src-p2-b0', 'doc-1', 2, 0, 'upper bound theorem', 'hash-2', 'parser-v1');
            """
        )
    repository = PersistenceRepository(tmp_path / "annotation.sqlite3")
    assert repository.migrate_source_index(legacy) == 2
    assert repository.migrate_source_index(legacy) == 2
    assert repository.connection.execute("SELECT COUNT(*) FROM source_blocks").fetchone()[0] == 2
    rows = repository.connection.execute(
        """
        SELECT b.source_ref FROM source_blocks_fts f
        JOIN source_blocks b ON b.source_ref = f.source_ref
        WHERE source_blocks_fts MATCH 'theorem'
        """
    ).fetchall()
    assert [row[0] for row in rows] == ["src-p2-b0"]
    repository.close()
