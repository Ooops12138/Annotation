"""Load editable Markdown prompts used by workflow agents.

Prompt instructions live in ``annotation/prompts/*.md`` so they can be read and
edited without changing Python control flow.  Files use explicit double-brace
placeholders such as ``{{TEXTBOOK_CONTEXT}}`` for runtime context.
"""

from __future__ import annotations

import re
from pathlib import Path


_PROMPTS_DIR = Path(__file__).with_name("prompts")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_AGENT_NAME_RE = re.compile(r"[a-z0-9_]+")


def load_prompt(agent: str, **values: object) -> str:
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

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"missing value for prompt placeholder {key!r} in {path}")
        return str(values[key])

    return _PLACEHOLDER_RE.sub(replace, template).strip()
