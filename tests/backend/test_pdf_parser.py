from pathlib import Path

from annotation.ingestion.pdf_parser import PARSER_VERSION, extraction_warnings, parse_pdf


def test_parse_fixture_pdf_to_traceable_blocks() -> None:
    pdfs = sorted(Path("books").glob("*.pdf"))
    if not pdfs:
        return
    document, blocks = parse_pdf(pdfs[0], run_id="test-run")
    assert document.run_id == "test-run"
    assert blocks
    first = blocks[0]
    assert first.source_ref in first.source_refs
    assert first.page_number >= 1
    assert len(first.text_hash) == 64
    assert first.parser_version == PARSER_VERSION
    assert len(first.bbox) == 4
