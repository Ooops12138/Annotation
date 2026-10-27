"""Load editable Markdown prompts used by workflow agents.

Prompt instructions live in ``annotation/prompts/*.md`` so they can be read and
edited without changing Python control flow.  Files use explicit double-brace
placeholders such as ``{{TEXTBOOK_CONTEXT}}`` for runtime context.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


_PROMPTS_DIR = Path(__file__).with_name("prompts")
_PROMPT_LAYERS_DIR = Path(__file__).with_name("prompt_layers")
_PROMPT_LAYER_MANIFEST = _PROMPT_LAYERS_DIR / "manifest.json"
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_AGENT_NAME_RE = re.compile(r"[a-z0-9_]+")
_LAYER_SLOT_NAMES = {
    "DOMAIN_RULES",
    "TEACHING_GUIDANCE",
    "RETRIEVAL_RULES",
    "EVIDENCE_RULES",
    "INTERACTIVE_CRITIQUE_RULES",
}


def load_prompt(agent: str, *, profile: str | None = None, **values: object) -> str:
    """Read an agent's Markdown prompt and render its named placeholders.

    ``agent`` is the prompt filename without the ``.md`` suffix.  Missing
    placeholders are rejected so a malformed prompt cannot silently reach a
    model with an incomplete context.
    """

    if not _AGENT_NAME_RE.fullmatch(agent):
        raise ValueError(f"invalid prompt agent name: {agent!r}")
    path = _PROMPTS_DIR / f"{agent}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt file not found for agent {agent!r}: {path}")
    template = path.read_text(encoding="utf-8")
    template = _expand_profile_slots(agent, template, profile)
    # Shared contracts are expanded before runtime data, never recursively in it.
    for key, filename in {
        "TRUST_BOUNDARY": "trust_boundary",
        "CONTENT_RULES": "content_rules",
        "QUIZ_RULES": "quiz_rules",
        "NOTATION_CONTRACT": "notation_contract",
    }.items():
        if "{{" + key + "}}" in template:
            template = template.replace("{{" + key + "}}", (_PROMPTS_DIR / "shared" / f"{filename}.md").read_text(encoding="utf-8").strip())
    if "{{OUTPUT_SCHEMA}}" in template:
        from annotation.workflow.models import ContentDraft
        from annotation.domain.artifacts import QuizDraft

        schema = {
            "generate_content_artifact": ContentDraft,
            "revise_content_artifact": ContentDraft,
            "revise_content_for_fact_check": ContentDraft,
            "generate_quiz_artifact": QuizDraft,
            "regenerate_quiz_for_fact_check": QuizDraft,
        }[agent]
        template = template.replace("{{OUTPUT_SCHEMA}}", json.dumps(schema.model_json_schema(), ensure_ascii=True, indent=2))

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"missing value for prompt placeholder {key!r} in {path}")
        return str(values[key])

    return _PLACEHOLDER_RE.sub(replace, template).strip()


def _expand_profile_slots(agent: str, template: str, profile: str | None) -> str:
    """Expand the allowlisted profile slots without changing legacy calls.

    ``profile=None`` uses the manifest default for the agent.  An empty string
    explicitly disables profile injection, which gives migration tests a
    direct legacy comparison path.  Profile files are read as data and are not
    recursively rendered as runtime placeholders.
    """

    slots_in_template = set(_PLACEHOLDER_RE.findall(template)) & _LAYER_SLOT_NAMES
    if not slots_in_template:
        return template
    if not _PROMPT_LAYER_MANIFEST.is_file():
        raise FileNotFoundError(f"prompt layer manifest not found: {_PROMPT_LAYER_MANIFEST}")
    manifest = _read_prompt_layer_manifest()
    agent_config = manifest.get("agents", {}).get(agent)
    if not isinstance(agent_config, dict):
        raise ValueError(f"prompt agent {agent!r} has no layer manifest entry")
    selected = agent_config.get("default_profile") if profile is None else profile
    if selected == "":
        return _replace_layer_slots(template, slots_in_template, {})
    if not isinstance(selected, str) or not selected:
        raise ValueError(f"invalid prompt profile for agent {agent!r}: {selected!r}")
    allowed_slots = set(agent_config.get("reads", []))
    if not slots_in_template <= allowed_slots:
        unknown = sorted(slots_in_template - allowed_slots)
        raise ValueError(f"prompt agent {agent!r} cannot read profile slots: {unknown}")
    profile_config = manifest.get("profiles", {}).get(selected)
    if not isinstance(profile_config, dict):
        raise ValueError(f"prompt profile not found: {selected!r}")
    slot_paths = profile_config.get("slots", {})
    values: dict[str, str] = {}
    for slot in slots_in_template:
        relative = slot_paths.get(slot)
        if not isinstance(relative, str) or not relative:
            raise ValueError(f"profile {selected!r} does not define slot {slot!r}")
        layer_path = (_PROMPT_LAYERS_DIR / relative).resolve()
        if _PROMPT_LAYERS_DIR.resolve() not in layer_path.parents:
            raise ValueError(f"profile slot path escapes prompt layer directory: {relative!r}")
        if not layer_path.is_file():
            raise FileNotFoundError(f"prompt profile file not found for {slot}: {layer_path}")
        values[slot] = layer_path.read_text(encoding="utf-8").strip()
    return _replace_layer_slots(template, slots_in_template, values)


def _read_prompt_layer_manifest() -> dict[str, object]:
    try:
        manifest = json.loads(_PROMPT_LAYER_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid prompt layer manifest: {_PROMPT_LAYER_MANIFEST}") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("prompt layer manifest must be an object with version=1")
    profiles = manifest.get("profiles")
    agents = manifest.get("agents")
    if not isinstance(profiles, dict) or not isinstance(agents, dict):
        raise ValueError("prompt layer manifest requires object profiles and agents")
    for profile_name, profile_config in profiles.items():
        if not isinstance(profile_name, str) or not isinstance(profile_config, dict):
            raise ValueError("prompt layer profile entries must be objects")
        slots = profile_config.get("slots")
        if not isinstance(slots, dict) or not set(slots) <= _LAYER_SLOT_NAMES:
            raise ValueError(f"profile {profile_name!r} contains unknown or missing slots")
        if not all(isinstance(slot_path, str) and slot_path for slot_path in slots.values()):
            raise ValueError(f"profile {profile_name!r} has invalid slot paths")
    for agent_name, agent_config in agents.items():
        if not isinstance(agent_name, str) or not isinstance(agent_config, dict):
            raise ValueError("prompt layer agent entries must be objects")
        reads = agent_config.get("reads", [])
        if not isinstance(reads, list) or not set(reads) <= _LAYER_SLOT_NAMES:
            raise ValueError(f"agent {agent_name!r} contains unknown profile slots")
        default_profile = agent_config.get("default_profile")
        if default_profile is not None and not isinstance(default_profile, str):
            raise ValueError(f"agent {agent_name!r} has an invalid default_profile")
    return manifest


def _replace_layer_slots(template: str, slots: set[str], values: dict[str, str]) -> str:
    for slot in slots:
        template = template.replace("{{" + slot + "}}", values.get(slot, ""))
    return template
