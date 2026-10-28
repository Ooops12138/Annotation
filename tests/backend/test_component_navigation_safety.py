import json
import os
import subprocess

import pytest
from pydantic import ValidationError

from annotation.config import PROJECT_ROOT
from annotation.domain.artifacts import GeneratedHtmlSpec


def _spec():
    return dict(component_type="generated_html", component_id="probe", title="Observe",
        learning_objective="Compare states", source_refs=["src-1"],
        accessibility=dict(aria_label="Observe", description="States", observation="Compare"),
        controls=[], test_actions=[], annotations=[], html='<p id="state">ready</p>', css="", javascript="")


@pytest.mark.parametrize("html", [
    '<meta http-equiv="refresh" content="0;url=https://example.test">',
    '<META HTTP-EQUIV=Refresh content="0;url=about:blank">',
    '<a href="https://example.test">go</a>', '<a HrEf = "#local">go</a>',
    '<area href="https://example.test">', '<base target="_top">',
    '<button onclick="loc&#97;tion=\'about:blank\'">go</button>',
])
def test_generated_html_rejects_navigation_markup(html):
    spec = _spec()
    spec["html"] = html
    with pytest.raises(ValidationError, match="navigate"):
        GeneratedHtmlSpec.model_validate(spec)


@pytest.mark.parametrize("script", [
    'location="about:blank"', 'window.location.assign("about:blank")',
    'window["location"]="about:blank"', 'navigation.navigate("about:blank")',
    'window.open("https://example.test")', 'anchor.setAttribute("href", "https://example.test")',
])
def test_generated_html_rejects_navigation_script(script):
    spec = _spec()
    spec["javascript"] = script
    with pytest.raises(ValidationError, match="navigate"):
        GeneratedHtmlSpec.model_validate(spec)


@pytest.mark.skipif(os.getenv("ANNOTATION_BROWSER_TESTS") != "1", reason="opt-in real Chromium sandbox")
@pytest.mark.parametrize("case,expected", [("safe", "passed"), ("navigation", "render_error"), ("event_loop", "timeout")])
def test_real_sandbox_navigation_and_interaction_deadline(tmp_path, case, expected):
    spec = _spec()
    if case == "navigation":
        # Deliberately evade the direct syntax check to test browser enforcement.
        spec["javascript"] = "setTimeout(() => window['loca'+'tion']='about:blank', 100)"
    if case == "event_loop":
        spec.update(controls=[dict(kind="range", control_id="value", label="Value", minimum=-1, maximum=1,
                                   step=1, default_value=0)],
            test_actions=[dict(action="set_range", control_id="value", value=1, expected_text="ready")],
            html='<input type="range" min="-1" max="1" value="0" data-component-control="value"><p>ready</p>',
            javascript="document.querySelector('input').oninput = e => { if (Number(e.target.value) === -1) { while (true) {} } }; ")
    GeneratedHtmlSpec.model_validate(spec)
    source = tmp_path / "input.json"
    report_path = tmp_path / "report.json"
    source.write_text(json.dumps(dict(spec=spec, timeout_ms=5000, output_dir=str(tmp_path))), encoding="utf-8")
    completed = subprocess.run(["node", str(PROJECT_ROOT / "web/scripts/run-interactive-sandbox.mjs"),
                                str(source), str(report_path)], capture_output=True, text=True, timeout=20)
    assert report_path.exists(), completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == expected, report
    if case == "navigation":
        assert any("navigation forbidden" in error for error in report["console_errors"])
