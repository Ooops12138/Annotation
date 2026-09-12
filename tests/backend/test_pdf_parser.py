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
    assert first.raw_text == first.text
    assert first.source_ref in document.source_refs
    assert "parser" in document.run_metadata


def test_parse_pdf_can_disable_optional_candidates_and_writes_json_markdown(tmp_path: Path) -> None:
    pdfs = sorted(Path("books").glob("*.pdf"))
    if not pdfs:
        return
    document, blocks = parse_pdf(pdfs[0], run_id="test-artifact", ocr_backend="none", formula_backend="none", artifact_dir=tmp_path)
    assert (tmp_path / "source.json").is_file()
    assert (tmp_path / "source.md").is_file()
    payload = __import__("json").loads((tmp_path / "source.json").read_text(encoding="utf-8"))
    assert payload["document"]["artifact_id"] == document.artifact_id
    assert payload["blocks"][0]["raw_text"] == blocks[0].raw_text
