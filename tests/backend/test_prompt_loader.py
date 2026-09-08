import pytest

from annotation.prompt_loader import load_prompt


def test_load_prompt_renders_markdown_agent_prompt() -> None:
    prompt = load_prompt("load_or_create_blueprint", TEXTBOOK_CONTEXT="[src-1] 定义")

    assert prompt.startswith("# Agent: load_or_create_blueprint")
    assert "[src-1] 定义" in prompt
    assert "{{TEXTBOOK_CONTEXT}}" not in prompt


def test_load_prompt_rejects_missing_placeholder_value() -> None:
    with pytest.raises(KeyError):
        load_prompt("generate_document_ir", TEXTBOOK_CONTEXT="excerpt")

