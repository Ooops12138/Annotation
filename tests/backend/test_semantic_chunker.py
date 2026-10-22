from annotation.ingestion.semantic_chunker import build_semantic_blocks
from annotation.domain.artifacts import SourceBlock


def block(ref, page, index, text, doc="doc"):
    return SourceBlock(artifact_id=ref, source_ref=ref, document_id=doc, run_id="run", version=1,
                       status="draft", source_refs=[ref], created_by="test", page_number=page,
                       block_index=index, text=text, raw_text=text, text_hash=f"h-{ref}",
                       parser_version="test", bbox=(0, 0, 1, 1))


def test_semantic_chunks_preserve_raw_blocks_and_lineage_and_page_boundary():
    raw = [block("b2", 1, 1, "第二段是重要说明。"), block("b1", 1, 0, "1.1 定义"),
           block("b3", 2, 0, "新页内容。")]
    chunks = build_semantic_blocks(raw, target_chars=100)
    assert [b.source_ref for b in raw] == ["b2", "b1", "b3"]
    assert chunks[0].source_refs == ["b1", "b2"]
    assert chunks[0].text == "1.1 定义\n第二段是重要说明。"
    assert chunks[1].source_refs == ["b3"]
    assert all(c.source_ref.startswith("doc-sem-") for c in chunks)


def test_semantic_chunker_rejects_invalid_limits_and_overlap():
    raw = [block("b1", 1, 0, "内容")]
    try:
        build_semantic_blocks(raw, target_chars=20, max_chars=10)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid chunk limits were accepted")
    try:
        build_semantic_blocks(raw, overlap_blocks=1)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported overlapping lineage was accepted")
