"""Bridge the A-004 workflow to the local shared-renderer Playwright sandbox."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from annotation.config import PROJECT_ROOT, STORAGE_DIR
from annotation.domain.artifacts import InteractiveComponentSandboxReport, InteractiveComponentSpec


SANDBOX_TIMEOUT_SECONDS = 10


def _report(*, status: str, error: str | None = None, duration_ms: int | None = None) -> InteractiveComponentSandboxReport:
    return InteractiveComponentSandboxReport(
        status=status,  # type: ignore[arg-type]
        error=error,
        duration_ms=duration_ms,
    )


def run_interactive_component_sandbox(
    spec: InteractiveComponentSpec,
    *,
    run_id: str,
    task_id: str,
    attempt: int,
    root: str | Path | None = None,
) -> InteractiveComponentSandboxReport:
    """Render a validated spec in Chromium and return an auditable report.

    The Node runner starts a local Vite server and imports the same renderer as
    the learner page. It receives JSON through files and is never passed model
    text as a shell command.
    """

    node = shutil.which("node")
    script = PROJECT_ROOT / "web" / "scripts" / "run-interactive-sandbox.mjs"
    started = time.monotonic()
    if node is None:
        return _report(
            status="browser_error",
            error="node executable is unavailable for the interactive component sandbox",
            duration_ms=round((time.monotonic() - started) * 1000),
        )
    if not script.is_file():
        return _report(
            status="browser_error",
            error=f"interactive sandbox runner is missing: {script}",
            duration_ms=round((time.monotonic() - started) * 1000),
        )

    safe_run = "".join(character if character.isalnum() or character in "._-" else "-" for character in run_id).strip(".-") or "run"
    safe_task = "".join(character if character.isalnum() or character in "._-" else "-" for character in task_id).strip(".-") or "task"
    output_root = Path(root or (STORAGE_DIR / "artifacts" / "interactive_components")) / safe_run
    output_root.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex[:10]
    input_path = output_root / f"sandbox-input-{safe_task}-a{attempt}-{nonce}.json"
    report_path = output_root / f"sandbox-report-{safe_task}-a{attempt}-{nonce}.json"
    input_path.write_text(
        json.dumps(
            {
                "spec": spec.model_dump(mode="json"),
                "timeout_ms": SANDBOX_TIMEOUT_SECONDS * 1000,
                "output_dir": str(output_root.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(
            [node, str(script), str(input_path), str(report_path)],
            cwd=PROJECT_ROOT / "web",
            capture_output=True,
            text=True,
            timeout=SANDBOX_TIMEOUT_SECONDS + 5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _report(
            status="timeout",
            error="interactive component sandbox process exceeded the 10 second budget",
            duration_ms=round((time.monotonic() - started) * 1000),
        )
    except OSError as exc:
        return _report(
            status="browser_error",
            error=f"unable to start interactive component sandbox: {exc}",
            duration_ms=round((time.monotonic() - started) * 1000),
        )

    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        report = InteractiveComponentSandboxReport.model_validate(payload)
    except (OSError, ValueError, TypeError) as exc:
        detail = completed.stderr.strip() or completed.stdout.strip() or str(exc)
        return _report(
            status="browser_error",
            error=f"interactive sandbox did not produce a valid report: {detail[:1200]}",
            duration_ms=round((time.monotonic() - started) * 1000),
        )
    if completed.returncode and report.status == "passed":
        report.status = "browser_error"
        report.error = (completed.stderr.strip() or completed.stdout.strip() or "sandbox exited with a non-zero code")[:1200]
    if report.duration_ms is None:
        report.duration_ms = round((time.monotonic() - started) * 1000)
    return report


__all__ = ["SANDBOX_TIMEOUT_SECONDS", "run_interactive_component_sandbox"]
