"""Shared mixed-language token estimates; never a claim of exact tokenization."""

import math
import os


DEFAULT_TOKEN_ESTIMATE_SCALE = 1.7


def base_token_estimate(text: str) -> float:
    ascii_count = sum(character.isascii() for character in text)
    return ascii_count / 4 + len(text) - ascii_count


def estimate_tokens(text: str) -> int:
    scale = float(os.getenv("TOKEN_ESTIMATE_SCALE", str(DEFAULT_TOKEN_ESTIMATE_SCALE)))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("TOKEN_ESTIMATE_SCALE must be finite and positive")
    return math.ceil(base_token_estimate(text) * scale)
