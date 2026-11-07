"""Fixed reference token counts for rough input budgets, independent of models."""

from functools import lru_cache

import tiktoken


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    """Estimate input size with one reference tokenizer for every model.

    This is a budget estimate; API usage remains the actual consumption record.
    """
    # Textbook examples containing special-token strings are ordinary text.
    return len(_encoding().encode_ordinary(text))
