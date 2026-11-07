from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

import pytest

from annotation.artifact_storage import run_directory, find_run_directory, execution_lock, update_run_info
from annotation.config import STORAGE_DIR, PROJECT_ROOT
from annotation.persistence import PersistenceRepository
from annotation.prompt_archive import write_artifact_json, read_artifact_json
from annotation.storage_cli import archive_execution, inventory
from annotation.workflow.checkpoints import ChannelPayloadSerializer, checkpoint_directory


def _id():
    return "storage-test-" + uuid.uuid4().hex[:12]


def test_test_storage_and_child_process_are_isolated():
    assert not STORAGE_DIR.is_relative_to(PROJECT_ROOT / "storage")
    result = subprocess.run([sys.executable, "-c",
        "from annotation.config import STORAGE_DIR, DATABASE_PATH, rag_chroma_path; import json; "
        "print(json.dumps([str(STORAGE_DIR),str(DATABASE_PATH),rag_chroma_path()]))"],
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")}, capture_output=True, text=True, check=True)
    root, database, chroma = map(Path, json.loads(result.stdout))
    assert root == STORAGE_DIR
    assert database.is_relative_to(root)
    assert chroma.is_relative_to(root)


def test_readable_run_directory_is_stable_across_processes():
    run_id = _id()
    directory = run_directory(run_id, title="高等数学", pdf_path="textbook.pdf")
    assert "高等数学" in directory.name
    assert run_id in directory.name
    assert run_directory(run_id, title="different") == directory
    assert find_run_directory(run_id) == directory
    update_run_info(run_id, status="interrupted", stage="content", errors=["test reason"])
    assert "interrupted" in (directory / "README.md").read_text(encoding="utf-8")
    result = subprocess.run([sys.executable, "-c",
        "from annotation.artifact_storage import run_directory; import sys; print(run_directory(sys.argv[1]))", run_id],
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", check=True)
    assert Path(result.stdout.strip()) == directory


def test_diagnostic_split_is_lossless_deduplicated_and_does_not_mutate_payload():
    directory = run_directory(_id(), title="diagnostic")
    text = "教材讲解与完整调用记录" * 2000
    payload = {"content_artifacts": [{"markdown": "readable final result"}],
               "content_loop_traces": [{"task_id": "task-1", "attempts": [
                   {"prompt": text, "raw_output": text, "candidate": {"markdown": text}}]}]}
    path = write_artifact_json(directory / "results" / "content" / "content.json", payload)
    compact = json.loads(path.read_text(encoding="utf-8"))
    assert compact["content_artifacts"] == payload["content_artifacts"]
    assert "$artifact_ref" in compact["content_loop_traces"][0]
    assert list((directory / "traces" / "content_loop_traces").glob("task-1-*.json"))
    assert payload["content_loop_traces"][0]["attempts"][0]["prompt"] == text
    assert read_artifact_json(path) == payload
    before = sorted(str(p) for p in directory.rglob("*") if p.is_file())
    write_artifact_json(path, payload)
    assert sorted(str(p) for p in directory.rglob("*") if p.is_file()) == before
    # Identical prompt/raw text share a compressed object even across stage dirs.
    write_artifact_json(directory / "results" / "quiz" / "quiz.json", {"raw_output": text})
    assert len(list((directory / "blobs").glob("*.gz"))) == 2  # text + candidate


def test_corrupt_and_escaping_diagnostic_references_are_rejected(tmp_path):
    directory = run_directory(_id())
    path = write_artifact_json(directory / "results" / "result.json", {"raw_output": "long " * 1000})
    compact = json.loads(path.read_text(encoding="utf-8"))
    ref = compact["raw_output"]["$artifact_ref"]
    blob = (path.parent / ref["path"]).resolve()
    blob.write_bytes(b"corrupt")
    with pytest.raises((ValueError, OSError, EOFError)):
        read_artifact_json(path)
    ref["path"] = str(tmp_path / "outside.json")
    path.write_text(json.dumps(compact), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        read_artifact_json(path)


def test_large_pending_checkpoint_writes_are_compressed_and_legacy_payload_reads(tmp_path):
    serializer = ChannelPayloadSerializer(tmp_path / "channels")
    value = {"source": "long textbook " * 10000}
    kind, data = serializer.dumps_typed(value)
    assert kind == "annotation-blob-v1"
    assert serializer.loads_typed((kind, data)) == value
    assert len(list((tmp_path / "channels").iterdir())) == 1
    assert next((tmp_path / "channels").iterdir()).stat().st_size < 4096
    import gzip
    blob = next((tmp_path / "channels").iterdir())
    blob.write_bytes(gzip.decompress(blob.read_bytes()))
    assert serializer.loads_typed((kind, data)) == value


def test_legacy_checkpoints_remain_locatable_after_new_summary_is_created():
    run_id = _id()
    legacy = STORAGE_DIR / "artifacts" / "execution" / run_id
    legacy.mkdir(parents=True)
    (legacy / "checkpoints.sqlite3").write_bytes(b"legacy database")
    run_directory(run_id, title="legacy resume")
    assert checkpoint_directory(run_id) == legacy


def test_shared_pdf_source_reuses_parser_and_separates_configuration(tmp_path, monkeypatch):
    import pymupdf
    from annotation.ingestion import pdf_parser
    pdf = tmp_path / "small.pdf"
    with pymupdf.open() as book:
        page = book.new_page()
        page.insert_text((72, 72), "Chapter One. This is a textbook paragraph.")
        book.save(pdf)
    original = pdf_parser.pymupdf4llm.to_markdown
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(pdf_parser.pymupdf4llm, "to_markdown", counted)
    doc1, blocks1 = pdf_parser.parse_pdf(pdf, run_id="first", ocr_backend="none", formula_backend="none")
    doc2, blocks2 = pdf_parser.parse_pdf(pdf, run_id="second", ocr_backend="none", formula_backend="none")
    assert len(calls) == 1
    assert doc1.run_metadata["json_path"] == doc2.run_metadata["json_path"]
    assert doc2.run_id == "second" and all(b.run_id == "second" for b in blocks2)
    assert all(b.run_id == "first" for b in blocks1)
    assert [b.source_ref for b in blocks1] == [b.source_ref for b in blocks2]
    monkeypatch.setenv("ANNOTATION_SOURCE_CONFIG_VERSION", "different")
    doc3, _ = pdf_parser.parse_pdf(pdf, run_id="third", ocr_backend="none", formula_backend="none")
    assert len(calls) == 2 and doc3.run_metadata["json_path"] != doc1.run_metadata["json_path"]


def _terminal_run(tmp_path, status="succeeded"):
    repo = PersistenceRepository(tmp_path / "library.sqlite3")
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"test pdf")
    book = repo.register_book(pdf)
    run_id = _id()
    repo.create_run(book["book_id"], run_id=run_id)
    repo.update_run(run_id, status=status)
    directory = run_directory(run_id)
    execution = directory / "execution"
    execution.mkdir()
    (execution / "receipt.json").write_text("audit evidence" * 1000, encoding="utf-8")
    return repo, run_id, execution


def test_archival_preview_is_read_only_and_apply_preserves_evidence(tmp_path):
    repo, run_id, execution = _terminal_run(tmp_path)
    try:
        preview = archive_execution(run_id, database=Path(repo.database_path))
        assert preview["applied"] is False and execution.is_dir()
        assert not Path(preview["archive"]).exists()
        original = (execution / "receipt.json").read_bytes()
        result = archive_execution(run_id, apply=True, database=Path(repo.database_path))
        assert result["applied"] and not execution.exists()
        with zipfile.ZipFile(result["archive"]) as archive:
            assert archive.read("receipt.json") == original
        assert repo.get_run(run_id)["status"] == "succeeded"
    finally:
        repo.close()


@pytest.mark.parametrize("status", ["running", "stopping", "failed", "cancelled", "interrupted"])
def test_archival_protects_active_and_resumable_runs(tmp_path, status):
    repo, run_id, execution = _terminal_run(tmp_path, status)
    try:
        with pytest.raises(ValueError, match="protected"):
            archive_execution(run_id, apply=True, database=Path(repo.database_path))
        assert execution.exists()
    finally:
        repo.close()


def test_archival_refuses_live_execution_and_library_references(tmp_path):
    repo, run_id, execution = _terminal_run(tmp_path)
    try:
        with execution_lock(execution.parent):
            with pytest.raises(ValueError, match="active"):
                archive_execution(run_id, apply=True, database=Path(repo.database_path))
        repo.register_artifact(run_id, "custom", execution / "receipt.json")
        with pytest.raises(ValueError, match="references"):
            archive_execution(run_id, apply=True, database=Path(repo.database_path))
    finally:
        repo.close()


def test_inventory_covers_both_layouts_and_preserves_unknown_classification(tmp_path):
    (tmp_path / "content" / "test-blocked").mkdir(parents=True)
    (tmp_path / "content" / "test-blocked" / "content.json").write_bytes(b"123")
    (tmp_path / "execution" / "test-blocked").mkdir(parents=True)
    (tmp_path / "execution" / "test-blocked" / "receipt.json").write_bytes(b"12345")
    result = inventory(tmp_path)
    assert result["files"] == 2 and result["bytes"] == 8
    assert result["runs"][0]["status"] == "unknown"
    assert result["runs"][0]["bytes"] == 8
