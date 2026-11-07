import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from annotation.artifact_storage import run_directory
from annotation.domain.artifacts import ComponentAccessibility, GeneratedHtmlSpec
from annotation.workflow import interactive_sandbox


@pytest.mark.parametrize("explicit_root", [False, True])
def test_sandbox_output_belongs_to_run_and_attempts_do_not_overwrite(tmp_path, monkeypatch, explicit_root):
    run_id = "sandbox-storage-test"
    directory = run_directory(run_id, title="Sandbox storage")
    root = tmp_path / "custom" if explicit_root else None
    expected = root / run_id if root else directory / "traces" / "interactive_components"
    outputs = []

    def run(command, **kwargs):
        input_path, report_path = map(Path, command[-2:])
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        output = Path(payload["output_dir"])
        assert output.is_absolute() and output.parent == expected / "task-1"
        screenshot = output / "screenshot.png"
        screenshot.write_bytes(b"sandbox screenshot")
        report_path.write_text(json.dumps({"status": "passed", "screenshot_path": str(screenshot)}), encoding="utf-8")
        outputs.append(output)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(interactive_sandbox.shutil, "which", lambda _: "node")
    monkeypatch.setattr(interactive_sandbox.subprocess, "run", run)
    spec = GeneratedHtmlSpec(
        component_id="storage-test", title="Storage", learning_objective="Observe",
        source_refs=["source-1"],
        accessibility=ComponentAccessibility(aria_label="Storage", description="Storage", observation="Observe"),
        html="<p>Storage</p>", css="", javascript="",
    )
    for _ in range(2):
        report = interactive_sandbox.run_interactive_component_sandbox(
            spec, run_id=run_id, task_id="task-1", attempt=1, root=root,
        )
        assert report.status == "passed" and Path(report.screenshot_path).is_file()
    assert outputs[0] != outputs[1]
    assert all(list(output.glob("sandbox-report-*.json")) for output in outputs)
