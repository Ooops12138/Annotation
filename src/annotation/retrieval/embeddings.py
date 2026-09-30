"""Embedding provider boundary for the optional LlamaIndex/Chroma RAG path."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class EmbeddingProvider(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbeddingProvider:
    """Embedding client for APIs that expose OpenAI's /embeddings shape."""

    name = "openai-compatible-embeddings"

    def __init__(self, *, model: str, api_key: str, base_url: str | None = None) -> None:
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self.model, input=list(texts))
        return [list(item.embedding) for item in response.data]


class LocalSentenceTransformerEmbeddingProvider:
    """Embedding provider backed by a local sentence-transformers path."""

    name = "local-sentence-transformer"

    def __init__(self, model_path: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_path)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        values = self._model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, row)) for row in values]


def create_embedding_provider_from_env() -> EmbeddingProvider:
    from annotation.config import embedding_config

    config = embedding_config()
    if config["provider"] == "local":
        return LocalSentenceTransformerEmbeddingProvider(config["model_path"])
    return OpenAICompatibleEmbeddingProvider(
        model=config["model"],
        api_key=config["api_key"],
        base_url=config.get("base_url") or None,
    )


def create_llamaindex_embedding_from_env():
    """Create the LlamaIndex embedding object configured by ``.env``."""
    from annotation.config import embedding_config

    config = embedding_config()
    if config["provider"] == "local":
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding

        return HuggingFaceEmbedding(model_name=config["model_path"])
    from llama_index.embeddings.openai import OpenAIEmbedding

    return OpenAIEmbedding(
        model=config["model"],
        api_key=config["api_key"],
        api_base=config.get("base_url") or None,
    )


__all__ = [
    "EmbeddingProvider",
    "LocalSentenceTransformerEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "create_embedding_provider_from_env",
    "create_llamaindex_embedding_from_env",
]
