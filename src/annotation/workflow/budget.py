"""Conservative, provider-neutral request budgets (no tokenizer dependency)."""

import json
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from annotation.config import model_max_output_tokens
from annotation.providers import ProviderError


def encoded_size(text: str) -> int:
    # UTF-8 byte count is deliberately conservative for byte-based tokenizers.
    # This is an estimate, not provider-reported token usage.
    return len(text.encode("utf-8"))


def positive_setting(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class PromptBudget:
    input_tokens: int
    output_tokens: int

    @classmethod
    def for_provider(cls, provider: Any, *, output_limit: int | None = None) -> "PromptBudget":
        caps = getattr(provider, "capabilities", None)
        output = min(model_max_output_tokens(), output_limit or model_max_output_tokens())
        if getattr(caps, "max_output_tokens", None):
            output = min(output, caps.max_output_tokens)
        window = getattr(caps, "context_window", None)
        if window:
            output = min(output, max(1, window // 3))
        available = positive_setting("BLUEPRINT_INPUT_BUDGET_TOKENS", 16000)
        if window:
            available = min(available, window - output - 512)
        return cls(input_tokens=max(0, available), output_tokens=output)

    def estimate(self, prompt: str, schema: type[BaseModel]) -> int:
        return encoded_size(prompt) + encoded_size(json.dumps(schema.model_json_schema())) + 512

    def fits(self, prompt: str, schema: type[BaseModel]) -> bool:
        return self.estimate(prompt, schema) <= self.input_tokens

    def check(self, prompt: str, schema: type[BaseModel]) -> None:
        if not self.fits(prompt, schema):
            raise ProviderError("request exceeds the configured input/schema budget", category="budget")

