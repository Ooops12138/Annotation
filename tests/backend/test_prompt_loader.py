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


def test_load_content_artifact_prompt_renders_bounded_context() -> None:
    prompt = load_prompt(
        "generate_content_artifact",
        KNOWLEDGE_UNIT_CONTEXT='{"title":"上确界"}',
        CONTEXT_PACK="[src-1] 上界",
        ACCEPTANCE_CRITERIA='["覆盖目标"]',
    )
    assert prompt.startswith("# Agent: generate_content_artifact")
    assert "[src-1] 上界" in prompt
    assert "{{CONTEXT_PACK}}" not in prompt
