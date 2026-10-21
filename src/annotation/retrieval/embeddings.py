"""One embedding boundary shared by local/API clients and LlamaIndex."""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

LOCAL_MODEL = "BAAI/bge-small-zh-v1.5"
API_MODEL = "BAAI/bge-m3"
API_BASE_URL = "https://api.siliconflow.cn/v1"
BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class EmbeddingResponseError(ValueError):
    """The encoder did not return a usable, consistent embedding matrix."""


class EmbeddingProvider(Protocol):
    name: str

    @property
    def identity(self) -> dict: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def embedding_fingerprint(provider: EmbeddingProvider) -> str:
    return hashlib.sha256(json.dumps(provider.identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_embeddings(values, count: int, dimension: int | None = None) -> list[list[float]]:
    """Reject incomplete, ragged, nonfinite and zero vectors before persistence."""
    try:
        if len(values) != count:
            raise ValueError("embedding count mismatch")
        rows = []
        for value in values:
            if any(isinstance(x, (bool, str, bytes)) for x in value):
                raise ValueError("embedding values must be numbers")
            row = [float(x) for x in value]
            dimension = dimension or len(row)
            if not row or len(row) != dimension or not all(math.isfinite(x) for x in row):
                raise ValueError("invalid embedding dimension or numeric value")
            if not any(row):
                raise ValueError("embedding must not be a zero vector")
            rows.append(row)
        return rows
    except (ValueError, TypeError, OverflowError) as exc:
        raise EmbeddingResponseError(str(exc)) from exc


class OpenAICompatibleEmbeddingProvider:
    name = "openai-compatible-embeddings"

    def __init__(self, *, model: str, api_key: str, base_url: str | None = None,
                 batch_size: int = 16, timeout: float = 30, max_retries: int = 2,
                 client=None) -> None:
        if not model or not api_key or batch_size < 1 or not math.isfinite(timeout) or timeout <= 0 or max_retries < 0:
            raise ValueError("invalid embedding API configuration")
        from openai import OpenAI

        self.model = model
        endpoint = urlsplit(base_url or "https://api.openai.com/v1")
        if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("embedding base URL must not contain credentials or query parameters")
        self.base_url = urlunsplit(endpoint).rstrip("/")
        self.batch_size = batch_size
        self._dimension: int | None = None
        self._client = client if client is not None else OpenAI(
            api_key=api_key, base_url=self.base_url, timeout=timeout, max_retries=max_retries,
        )

    @property
    def identity(self) -> dict:
        return {"provider": self.name, "model": self.model, "endpoint": self.base_url,
                "encoding": "float", "query_instruction": "", "version": 1}

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start:start + self.batch_size])
            response = self._client.embeddings.create(model=self.model, input=batch, encoding_format="float")
            items = response.data
            indices = [item.index for item in items]
            if (any(type(i) is not int for i in indices)
                    or sorted(indices) != list(range(len(batch)))):
                raise EmbeddingResponseError("embedding response indices are missing or duplicated")
            rows = validate_embeddings([item.embedding for item in sorted(items, key=lambda x: x.index)],
                                       len(batch), self._dimension)
            self._dimension = len(rows[0])
            vectors.extend(rows)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed_documents(texts)


class LocalSentenceTransformerEmbeddingProvider:
    name = "local-sentence-transformer"

    def __init__(self, model_path: str = LOCAL_MODEL, *, batch_size: int = 16,
                 query_instruction: str | None = None, revision: str | None = None) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        from sentence_transformers import SentenceTransformer

        self.model = model_path
        self.batch_size = batch_size
        self._model = SentenceTransformer(model_path, device="cpu", revision=revision,
                                          trust_remote_code=False)
        self.query_instruction = (BGE_QUERY_INSTRUCTION if "bge-" in model_path.lower() and "zh" in model_path.lower()
                                  else "") if query_instruction is None else query_instruction
        config = self._model[0].auto_model.config
        self.revision = getattr(config, "_commit_hash", None) or revision or "local"
        if Path(model_path).is_dir():
            digest = hashlib.sha256()
            for path in sorted(Path(model_path).rglob("*")):
                if path.is_file() and path.suffix in {".json", ".txt", ".safetensors", ".bin"}:
                    digest.update(path.relative_to(model_path).as_posix().encode())
                    with path.open("rb") as stream:
                        digest.update(hashlib.file_digest(stream, "sha256").digest())
            self.revision = "local-sha256:" + digest.hexdigest()

    @property
    def identity(self) -> dict:
        return {"provider": self.name, "model": self.model, "revision": self.revision,
                "device": "cpu", "normalize": True, "max_length": self._model.max_seq_length,
                "query_instruction": self.query_instruction, "version": 1}

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        lengths = self._model.tokenizer(list(texts), truncation=False, padding=False)["input_ids"]
        if any(len(tokens) > self._model.max_seq_length for tokens in lengths):
            raise ValueError("text exceeds local model token limit; choose a longer-context encoder")
        values = self._model.encode(list(texts), batch_size=self.batch_size,
                                    normalize_embeddings=True, show_progress_bar=False, prompt="")
        return validate_embeddings(values, len(texts))

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([self.query_instruction + text])[0]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed_documents(texts)


def create_embedding_provider_from_env() -> EmbeddingProvider:
    """Auto chooses once, before indexing; searches never switch vector spaces."""
    from annotation.config import embedding_config

    config = embedding_config()
    if config["provider"] in {"api", "auto"} and config["api_key"]:
        try:
            provider = OpenAICompatibleEmbeddingProvider(
                model=config["model"], api_key=config["api_key"], base_url=config["base_url"],
                batch_size=int(config["batch_size"]), timeout=float(config["timeout"]),
                max_retries=int(config["max_retries"]),
            )
            if config["provider"] == "auto":
                provider.embed_query("检索连接测试")
            return provider
        except Exception:
            if config["provider"] != "auto":
                raise
            logging.getLogger(__name__).warning("Embedding API initialization failed; selecting local CPU encoder")
    elif config["provider"] == "auto":
        logging.getLogger(__name__).warning("No embedding API key; selecting local CPU encoder")
    return LocalSentenceTransformerEmbeddingProvider(
        config["model_path"], batch_size=int(config["batch_size"]),
        revision=config["revision"] or None, query_instruction=config.get("query_instruction"),
    )


def create_llamaindex_embedding(provider: EmbeddingProvider):
    """Lazy bridge: documents and queries use the same provider with distinct roles."""
    from llama_index.core.base.embeddings.base import BaseEmbedding
    from pydantic import PrivateAttr

    class ProviderEmbedding(BaseEmbedding):
        _provider: object = PrivateAttr()

        def __init__(self):
            super().__init__(model_name=embedding_fingerprint(provider), embed_batch_size=16)
            self._provider = provider

        def _get_query_embedding(self, query):
            return self._provider.embed_query(query)

        async def _aget_query_embedding(self, query):
            import asyncio
            return await asyncio.to_thread(self._get_query_embedding, query)

        def _get_text_embedding(self, text):
            return self._provider.embed_documents([text])[0]

        def _get_text_embeddings(self, texts):
            return self._provider.embed_documents(texts)

    return ProviderEmbedding()


def create_llamaindex_embedding_from_env():
    return create_llamaindex_embedding(create_embedding_provider_from_env())
