from pathlib import Path

from annotation.ingestion.hybrid import resolve_formula_backend, resolve_text_backend


def test_hybrid_backends_are_explicit_when_optional_runtime_is_missing_or_present() -> None:
    backend = resolve_text_backend("none")
    result = backend.recognize(Path("missing-region.png"))
    assert result["status"] == "unavailable"
    assert result["text"] is None


def test_formula_backend_requires_image_placeholder() -> None:
    backend = resolve_formula_backend("python recognizer.py")
    result = backend.recognize(Path("region.png"))
    assert result["status"] == "unavailable"
    assert "placeholder" in " ".join(result["warnings"])
