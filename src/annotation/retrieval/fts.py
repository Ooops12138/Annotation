"""SQLite FTS5 index for traceable textbook source blocks.

The index is deliberately small and local: it stores only the searchable
projection of ``SourceBlock`` artifacts.  The canonical source text remains
on the artifact itself, while every search result returns the original
``source_ref`` so callers can resolve the exact page/block evidence.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from annotation.domain.artifacts import SourceBlock


@dataclass(frozen=True)
class SourceSearchResult:
    source_ref: str
    document_id: str
    page_number: int
    block_index: int
    text: str
    rank: float


def _connect(database: str | sqlite3.Connection) -> sqlite3.Connection:
    if isinstance(database, sqlite3.Connection):
        connection = database
    else:
        connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_fts(database: str | sqlite3.Connection) -> None:
    """Create the FTS5 table and its source metadata table if absent."""
    connection = _connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_blocks (
                source_ref TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                block_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                parser_version TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS source_blocks_fts USING fts5(
                source_ref UNINDEXED,
                text
            );
            """
        )
        connection.commit()
    finally:
        if not isinstance(database, sqlite3.Connection):
            connection.close()


def index_source_blocks(
    database: str | sqlite3.Connection,
    blocks: Iterable[SourceBlock],
) -> int:
    """Upsert source blocks and rebuild the corresponding FTS rows.

    Returns the number of indexed blocks.  Re-indexing the same PDF/run is
    idempotent and never changes the source references.
    """
    connection = _connect(database)
    rows = list(blocks)
    try:
        initialize_fts(connection)
        for block in rows:
            connection.execute(
                """
                INSERT INTO source_blocks
                    (source_ref, document_id, page_number, block_index, text,
                     text_hash, parser_version)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_ref) DO UPDATE SET
                    document_id=excluded.document_id,
                    page_number=excluded.page_number,
                    block_index=excluded.block_index,
                    text=excluded.text,
                    text_hash=excluded.text_hash,
                    parser_version=excluded.parser_version
                """,
                (
                    block.source_ref,
                    block.document_id,
                    block.page_number,
                    block.block_index,
                    block.text,
                    block.text_hash,
                    block.parser_version,
                ),
            )
        # Keep the standalone FTS table in sync.  A contentless external
        # content table is intentionally avoided because some SQLite builds
        # report a malformed image when deleting/rebuilding it in memory.
        connection.execute("DELETE FROM source_blocks_fts")
        connection.execute(
            """
            INSERT INTO source_blocks_fts(rowid, source_ref, text)
            SELECT rowid, source_ref, text FROM source_blocks
            """
        )
        connection.commit()
        return len(rows)
    finally:
        if not isinstance(database, sqlite3.Connection):
            connection.close()


def search_source_blocks(
    database: str | sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
) -> list[SourceSearchResult]:
    """Search indexed source text and return traceable page/block hits."""
    if not query.strip():
        return []
    connection = _connect(database)
    try:
        initialize_fts(connection)
        rows = connection.execute(
            """
            SELECT b.source_ref, b.document_id, b.page_number, b.block_index,
                   b.text, bm25(source_blocks_fts) AS rank
            FROM source_blocks_fts f
            JOIN source_blocks b ON b.source_ref = f.source_ref
            WHERE source_blocks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, max(1, min(limit, 100))),
        ).fetchall()
        # The default SQLite tokenizer does not segment CJK text.  If a
        # Chinese query produces no token hit, use a normalized substring
        # lookup as a deterministic fallback while preserving the same
        # traceable result shape.
        if not rows:
            normalized_query = "".join(query.split())
            candidates = connection.execute(
                "SELECT source_ref, document_id, page_number, block_index, text FROM source_blocks"
            ).fetchall()
            rows = [
                {
                    "source_ref": row["source_ref"],
                    "document_id": row["document_id"],
                    "page_number": row["page_number"],
                    "block_index": row["block_index"],
                    "text": row["text"],
                    "rank": 0.0,
                }
                for row in candidates
                if normalized_query in "".join(row["text"].split())
            ][: max(1, min(limit, 100))]
        return [
            SourceSearchResult(
                source_ref=row["source_ref"],
                document_id=row["document_id"],
                page_number=row["page_number"],
                block_index=row["block_index"],
                text=row["text"],
                rank=float(row["rank"]),
            )
            for row in rows
        ]
    finally:
        if not isinstance(database, sqlite3.Connection):
            connection.close()
