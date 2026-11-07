import json

import pytest
import tiktoken
from pydantic import BaseModel

from annotation.providers import ProviderError
from annotation.token_counting import count_tokens
from annotation.workflow.budget import PromptBudget, encoded_size


@pytest.mark.parametrize("text", ["", "abcd", "中文教材", r"$\frac{x}{y}$", "🙂\n<|endoftext|>"])
def test_reference_counts_are_bpe_tokens_not_character_estimates(text, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MODEL_NAME", "deepseek-flash")
    expected = len(tiktoken.get_encoding("o200k_base").encode_ordinary(text))
    assert count_tokens(text) == expected
    assert encoded_size(text) == expected


def test_switching_models_does_not_change_budget_counts(monkeypatch):
    text = "中文教材🙂"
    expected = count_tokens(text)
    for provider, model in [("openai", "gpt-4"), ("openai", "gpt-4o"),
                            ("deepseek", "deepseek-flash"), ("custom", "unknown")]:
        monkeypatch.setenv("MODEL_PROVIDER", provider)
        monkeypatch.setenv("MODEL_NAME", model)
        assert count_tokens(text) == expected
        assert encoded_size(text) == expected


def test_prompt_budget_counts_schema_and_enforces_exact_boundary():
    class Output(BaseModel):
        text: str

    prompt = "教材讲解与公式 $x^2$"
    required = count_tokens(prompt) + count_tokens(json.dumps(Output.model_json_schema())) + 512
    assert PromptBudget(required, 100).fits(prompt, Output)
    with pytest.raises(ProviderError, match="input/schema budget"):
        PromptBudget(required - 1, 100).check(prompt, Output)
