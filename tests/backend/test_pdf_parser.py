from pathlib import Path

import pymupdf

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


def test_parser_captures_bookmarks_and_empty_pages_for_structure(tmp_path: Path) -> None:
    import pymupdf
    from annotation.ingestion.structure import build_textbook_structure
    pdf = tmp_path / "structured.pdf"
    with pymupdf.open() as document:
        first = document.new_page()
        first.insert_text((72, 72), "Chapter 1 Numbers")
        first.insert_text((72, 120), "1.1 Natural numbers")
        document.new_page()  # extraction gap is visible, not silently omitted
        third = document.new_page()
        third.insert_text((72, 72), "Chapter 2 Limits")
        document.set_toc([[1, "Chapter 1 Numbers", 1], [2, "1.1 Natural numbers", 1], [1, "Chapter 2 Limits", 3]])
        document.save(pdf)
    parsed, blocks = parse_pdf(pdf, run_id="bookmarks", ocr_backend="none", formula_backend="none", artifact_dir=tmp_path / "artifacts")
    assert parsed.run_metadata["page_count"] == 3
    assert parsed.run_metadata["empty_text_pages"] == [2]
    tree = build_textbook_structure(parsed, blocks)
    assert [node.title for node in tree.nodes[1:]] == ["Chapter 1 Numbers", "1.1 Natural numbers", "Chapter 2 Limits"]
    assert all(node.origin == "bookmark" for node in tree.nodes[1:])
    assert "page_without_extracted_text:2" in tree.warnings


def test_markdown_layout_preserves_table_coordinates_and_stable_refs(tmp_path: Path) -> None:
    pdf = tmp_path / "table.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Reference table", fontsize=22)
        for x in (72, 220, 380):
            page.draw_line((x, 110), (x, 210))
        for y in (110, 160, 210):
            page.draw_line((72, y), (380, y))
        for point, text in [((80, 140), "Name"), ((230, 140), "Value"),
                            ((80, 190), "Alpha"), ((230, 190), "42")]:
            page.insert_text(point, text)
        document.save(pdf)
    parsed, blocks = parse_pdf(pdf, artifact_dir=tmp_path / "artifacts")
    again, repeated = parse_pdf(pdf, run_id="second-run", artifact_dir=tmp_path / "artifacts")
    assert parsed.artifact_id == again.artifact_id
    assert [b.source_ref for b in blocks] == [b.source_ref for b in repeated]
    assert len({b.source_ref for b in blocks}) == len(blocks)
    assert all(b.source_ref != f"{parsed.artifact_id}-p{b.page_number}-b{b.block_index}" for b in blocks)
    assert parsed.run_metadata["extractor"] == "pymupdf4llm"
    table = next(b for b in blocks if b.run_metadata["layout_class"] == "table")
    assert "|" in table.text and "Alpha" in table.text and "42" in table.text
    # Layout boxes bound the cell text rather than the drawn table borders.
    x0, y0, x1, y1 = table.bbox
    assert 72 <= x0 <= 80 < 230 < x1 <= 380
    assert 110 <= y0 < 140 < 190 < y1 <= 210
    assert table.raw_text == table.text
    markdown = Path(parsed.run_metadata["markdown_path"]).read_text(encoding="utf-8")
    assert table.text in markdown
    assert parsed.run_metadata["json_path"] != again.run_metadata["json_path"]


def test_markdown_formula_still_uses_explicit_region_candidates(tmp_path: Path, monkeypatch) -> None:
    import annotation.ingestion.pdf_parser as parser

    class TextBackend:
        name = "test-ocr"
        available = True

        def recognize(self, image_path):
            assert image_path.is_file()
            return {"status": "ok", "recognizer": self.name, "text": "x = 2"}

    monkeypatch.setattr(parser, "resolve_text_backend", lambda name: TextBackend())
    pdf = tmp_path / "formula.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "x = 2")
        document.save(pdf)
    _, blocks = parse_pdf(pdf, formula_backend="none", artifact_dir=tmp_path / "artifacts")
    formula = next(b for b in blocks if b.run_metadata["block_type"] == "formula")
    assert formula.ocr_candidates[0].text == "x = 2"
    assert formula.ocr_candidates[0].source_ref == formula.source_ref
    assert formula.formula_latex_candidates[0].status == "unavailable"


def test_image_only_page_does_not_enable_implicit_ocr(tmp_path: Path) -> None:
    pdf = tmp_path / "scanned.pdf"
    with pymupdf.open() as document:
        # Rasterize visible text so the PDF has no native text layer.
        original = document.new_page()
        original.insert_text((72, 72), "Image text must not trigger automatic OCR")
        image = original.get_pixmap().tobytes("png")
        scanned = document.new_page()
        scanned.insert_image(scanned.rect, stream=image)
        document.delete_page(0)
        document.save(pdf)
    parsed, blocks = parse_pdf(pdf, ocr_backend="none", formula_backend="none", artifact_dir=tmp_path / "artifacts")
    assert not blocks
    assert parsed.run_metadata["empty_text_pages"] == [1]
    assert parsed.warnings
