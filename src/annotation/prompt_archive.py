"""Lossless, content-addressed prompt storage for JSON audit artifacts."""

import gzip
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PromptReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storage: Literal["gzip-sha256-v1"] = "gzip-sha256-v1"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: str
    characters: int = Field(ge=0)


PROMPT_FIELDS = {"prompt", "generation_prompt", "revision_prompt", "critic_prompt", "system_prompt", "cached_prompt"}


def archive_prompts(value: Any, directory: Path) -> Any:
    """Copy serialized payloads; never alter checkpoint/runtime state."""
    if isinstance(value, list):
        return [archive_prompts(item, directory) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in PROMPT_FIELDS and isinstance(item, str) and len(item) >= 512:
            digest = hashlib.sha256(item.encode("utf-8")).hexdigest()
            relative = Path("prompts") / f"{digest}.txt.gz"
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
                temporary.write_bytes(gzip.compress(item.encode("utf-8"), mtime=0))
                temporary.replace(path)
            result[key] = PromptReference(sha256=digest, path=relative.as_posix(), characters=len(item)).model_dump()
        else:
            result[key] = archive_prompts(item, directory)
    return result


def restore_prompts(value: Any, directory: Path) -> Any:
    if isinstance(value, list):
        return [restore_prompts(item, directory) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in PROMPT_FIELDS and isinstance(item, dict) and item.get("storage") == "gzip-sha256-v1":
            ref = PromptReference.model_validate(item)
            expected = f"prompts/{ref.sha256}.txt.gz"
            if ref.path != expected:
                raise ValueError("invalid prompt archive path")
            path = (directory / ref.path).resolve()
            if not path.is_relative_to(directory.resolve()):
                raise ValueError("prompt archive escapes artifact directory")
            with gzip.open(path, "rb") as handle:
                data = handle.read(10_000_001)
            if len(data) > 10_000_000 or hashlib.sha256(data).hexdigest() != ref.sha256:
                raise ValueError("invalid prompt archive digest or size")
            text = data.decode("utf-8")
            if len(text) != ref.characters:
                raise ValueError("invalid prompt archive character count")
            result[key] = text
        else:
            result[key] = restore_prompts(item, directory)
    return result


def write_artifact_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    compact = archive_prompts(payload, path.parent)
    path.write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_artifact_json(path: Path) -> dict:
    return restore_prompts(json.loads(path.read_text(encoding="utf-8")), path.parent)
