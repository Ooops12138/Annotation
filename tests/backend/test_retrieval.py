import sqlite3

from annotation.domain.artifacts import SourceBlock
from annotation.retrieval import index_source_blocks, search_source_blocks


def test_fts5_returns_traceable_source_refs() -> None:
    connection = sqlite3.connect(":memory:")
    block = SourceBlock(
        artifact_id="artifact-1",
        source_ref="src-p2-b3",
        document_id="doc-1",
        run_id="run-1",
        version=1,
        status="draft",
        source_refs=["src-p2-b3"],
        created_by="test",
        page_number=2,
        block_index=3,
        text="完全公理保证非空有上界集合存在上确界。",
        text_hash="hash",
        parser_version="test",
        bbox=(0, 0, 1, 1),
    )
    assert index_source_blocks(connection, [block]) == 1
    results = search_source_blocks(connection, "上确界")
    assert results and results[0].source_ref == "src-p2-b3"
    assert results[0].page_number == 2
