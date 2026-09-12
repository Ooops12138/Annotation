"""Evidence-preserving local OCR helpers for Annotation Hybrid PDF extraction.

The native PDF text remains authoritative evidence.  OCR is only run on
rendered local regions that are formula candidates or contain suspicious
characters, and its output is stored as a review candidate.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class OCRBackend(Protocol):
    name: str
    available: bool

    def recognize(self, image_path: Path) -> dict[str, Any]: ...


@dataclass
class UnavailableBackend:
    name: str
    reason: str
    available: bool = False

    def recognize(self, image_path: Path) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "recognizer": self.name,
            "text": None,
            "latex": None,
            "confidence": None,
            "warnings": [self.reason],
        }


class RapidOCRBackend:
    name = "rapidocr-onnxruntime"
    available = True

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]

        self.engine = RapidOCR()

    def recognize(self, image_path: Path) -> dict[str, Any]:
        try:
            result, _ = self.engine(str(image_path))
            items: list[dict[str, Any]] = []
            for item in result or []:
                if len(item) < 3:
                    continue
                items.append({"text": str(item[1]), "confidence": float(item[2])})
            text = "\n".join(item["text"] for item in items).strip() or None
            confidence = (
                round(sum(item["confidence"] for item in items) / len(items), 4)
                if items
                else None
            )
            return {
                "status": "success" if text else "empty",
                "recognizer": self.name,
                "text": text,
                "latex": None,
                "confidence": confidence,
                "items": items,
                "warnings": [],
            }
        except Exception as exc:  # pragma: no cover - depends on local model/runtime
            return {
                "status": "error",
                "recognizer": self.name,
                "text": None,
                "latex": None,
                "confidence": None,
                "warnings": [f"ocr_runtime_error: {exc}"],
            }


class TesseractBackend:
    name = "tesseract"
    available = True

    def __init__(self, executable: str) -> None:
        self.executable = executable

    def recognize(self, image_path: Path) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                [self.executable, str(image_path), "stdout", "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            text = completed.stdout.strip() or None
            warnings = [] if completed.returncode == 0 else [completed.stderr.strip()]
            return {
                "status": "success" if text and completed.returncode == 0 else "empty",
                "recognizer": self.name,
                "text": text,
                "latex": None,
                "confidence": None,
                "warnings": [warning for warning in warnings if warning],
            }
        except Exception as exc:  # pragma: no cover - depends on local executable
            return {
                "status": "error",
                "recognizer": self.name,
                "text": None,
                "latex": None,
                "confidence": None,
                "warnings": [f"ocr_runtime_error: {exc}"],
            }


class CommandFormulaBackend:
    """Adapter for a local formula recognizer command.

    The command template must contain ``{image}``. Its stdout may be plain
    LaTeX/text or a JSON object containing ``latex``, ``text`` and
    ``confidence``.  This keeps pix2tex/UniMERNet-style tools replaceable.
    """

    name = "formula-command"
    available = True

    def __init__(self, template: str) -> None:
        self.template = template

    def recognize(self, image_path: Path) -> dict[str, Any]:
        try:
            command = shlex.split(self.template.format(image=shlex.quote(str(image_path))))
            completed = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
            output = completed.stdout.strip()
            if completed.returncode != 0:
                return {
                    "status": "error",
                    "recognizer": self.name,
                    "text": None,
                    "latex": None,
                    "confidence": None,
                    "warnings": [completed.stderr.strip() or f"returncode={completed.returncode}"],
                }
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError:
                parsed = {"latex": output} if output else {}
            latex = parsed.get("latex") or parsed.get("text")
            return {
                "status": "success" if latex else "empty",
                "recognizer": self.name,
                "text": parsed.get("text"),
                "latex": latex,
                "confidence": parsed.get("confidence"),
                "warnings": [],
            }
        except Exception as exc:  # pragma: no cover - depends on caller command
            return {
                "status": "error",
                "recognizer": self.name,
                "text": None,
                "latex": None,
                "confidence": None,
                "warnings": [f"formula_runtime_error: {exc}"],
            }


class Pix2TexBackend:
    """Local pix2tex LaTeX OCR adapter, initialized once per extraction run."""

    name = "pix2tex"
    available = True

    def __init__(self) -> None:
        from PIL import Image  # type: ignore[import-not-found]
        from pix2tex.cli import LatexOCR  # type: ignore[import-not-found]

        self._image = Image
        self.model = LatexOCR()

    def recognize(self, image_path: Path) -> dict[str, Any]:
        try:
            with self._image.open(image_path) as image:
                latex = str(self.model(image)).strip()
            return {
                "status": "success" if latex else "empty",
                "recognizer": self.name,
                "text": None,
                "latex": latex or None,
                "confidence": None,
                "warnings": [],
            }
        except Exception as exc:  # pragma: no cover - depends on model/runtime
            return {
                "status": "error",
                "recognizer": self.name,
                "text": None,
                "latex": None,
                "confidence": None,
                "warnings": [f"formula_runtime_error: {exc}"],
            }


def resolve_text_backend(preferred: str = "auto") -> OCRBackend:
    if preferred == "none":
        return UnavailableBackend("none", "ocr_disabled_by_configuration")
    if preferred in {"auto", "rapidocr"}:
        try:
            return RapidOCRBackend()
        except Exception as exc:
            if preferred == "rapidocr":
                return UnavailableBackend("rapidocr-onnxruntime", f"ocr_backend_unavailable: {exc}")
    if preferred in {"auto", "tesseract"}:
        executable = shutil.which("tesseract")
        if executable:
            return TesseractBackend(executable)
        if preferred == "tesseract":
            return UnavailableBackend("tesseract", "ocr_backend_unavailable: tesseract_not_found")
    return UnavailableBackend("none", "ocr_backend_unavailable: install rapidocr-onnxruntime or tesseract")


def resolve_formula_backend(command: str | None = None, preferred: str = "auto") -> OCRBackend:
    template = command or os.environ.get("ANNOTATION_FORMULA_OCR_COMMAND")
    if template:
        if "{image}" not in template:
            return UnavailableBackend("formula-command", "formula_command_missing_{image}_placeholder")
        return CommandFormulaBackend(template)
    if preferred == "none":
        return UnavailableBackend("formula-recognizer", "formula_backend_disabled_by_configuration")
    try:
        return Pix2TexBackend()
    except Exception as exc:
        return UnavailableBackend("pix2tex", f"formula_backend_unavailable: {exc}")
