"""Load editable Markdown prompts used by workflow agents.

Prompt instructions live in ``annotation/prompts/*.md`` so they can be read and
edited without changing Python control flow.  Files use explicit double-brace
placeholders such as ``{{TEXTBOOK_CONTEXT}}`` for runtime context.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar


_PROMPTS_DIR = Path(__file__).with_name("prompts")
_PROMPT_LAYERS_DIR = Path(__file__).with_name("prompt_layers")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_AGENT_NAME_RE = re.compile(r"[a-z0-9_]+")
_LAYER_SLOT_NAMES = {"DOMAIN_RULES", "TEACHING_GUIDANCE"}
_ACTIVE_PROFILE: ContextVar[str | None] = ContextVar("annotation_prompt_profile", default=None)


@contextmanager
def prompt_profile_scope(profile: str | None):
    """Bind one run's selection; callers dispatching threads copy the context."""
    token = _ACTIVE_PROFILE.set(profile)
    try:
        yield
    finally:
        _ACTIVE_PROFILE.reset(token)


def _read_prompt_layer_manifest() -> dict:
    path = _PROMPT_LAYERS_DIR / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != 2:
        raise ValueError("prompt layer manifest must be an object with version=2")
    profiles = manifest.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("prompt layer manifest requires object profiles")
    for name, config in profiles.items():
        if not isinstance(name, str) or not _AGENT_NAME_RE.fullmatch(name) or not isinstance(config, dict):
            raise ValueError("invalid prompt profile entry")
        if not isinstance(config.get("label"), str) or not config["label"].strip():
            raise ValueError(f"prompt profile {name!r} requires a label")
        slots = config.get("slots", {})
        if not isinstance(slots, dict) or not set(slots) <= _LAYER_SLOT_NAMES:
            raise ValueError(f"profile {name!r} contains unknown slots")
        if not all(isinstance(value, str) and value for value in slots.values()):
            raise ValueError(f"profile {name!r} has invalid slot paths")
    return profiles


def list_prompt_profiles() -> list[dict[str, str]]:
    """List local subject packages without invoking a model."""
    return [{"id": name, "label": config["label"]}
            for name, config in _read_prompt_layer_manifest().items()]


def _profile_rules(profile: str | None) -> dict[str, str]:
    if profile is None or profile == "":
        return {}
    if not isinstance(profile, str) or not _AGENT_NAME_RE.fullmatch(profile):
        raise ValueError(f"invalid prompt profile: {profile!r}")
    profiles = _read_prompt_layer_manifest()
    if profile not in profiles:
        raise ValueError(f"prompt profile not found: {profile!r}")
    rules = {}
    root = _PROMPT_LAYERS_DIR.resolve()
    for slot, relative in profiles[profile].get("slots", {}).items():
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError(f"profile slot path escapes prompt layer directory: {relative!r}")
        rules[slot] = path.read_text(encoding="utf-8").strip()
    return rules


def validate_prompt_profile(profile: str | None) -> str | None:
    """Reject unknown/damaged selected packages before starting model work."""
    _profile_rules(profile)
    return profile or None


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
    selected = _ACTIVE_PROFILE.get() if profile is None else profile
    rules = _profile_rules(selected)
    # Shared contracts are expanded before runtime data, never recursively in it.
    for key, filename in {
        "TRUST_BOUNDARY": "trust_boundary",
        "CONTENT_RULES": "content_rules",
        "QUIZ_RULES": "quiz_rules",
        "NOTATION_CONTRACT": "notation_contract",
        "RETRIEVAL_RULES": "retrieval_rules",
        "EVIDENCE_RULES": "evidence_rules",
        "BLUEPRINT_GUIDANCE": "blueprint_guidance",
        "GENERAL_TEACHING_GUIDANCE": "teaching_guidance",
        "INTERACTIVE_CRITIQUE_RULES": "interactive_critique_rules",
        "COMPONENT_CODE_RULES": "component_code_rules",
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
        if key in _LAYER_SLOT_NAMES:
            return rules.get(key, "")
        if key not in values:
            raise KeyError(f"missing value for prompt placeholder {key!r} in {path}")
        return str(values[key])

    # A single substitution pass keeps both plugin text and runtime data literal.
    return _PLACEHOLDER_RE.sub(replace, template).strip()
