"""Lossless, content-addressed prompt storage for JSON audit artifacts."""

import gzip
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from annotation.artifact_storage import atomic_json
import os


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
    run_root = _run_root(path.parent)
    compact = _archive_fields(payload, path.parent, run_root) if run_root else payload
    compact = archive_prompts(compact, path.parent)
    atomic_json(path, compact)
    return path


def read_artifact_json(path: Path) -> dict:
    return restore_prompts(_restore_fields(json.loads(path.read_text(encoding="utf-8")), path.parent), path.parent)


def _run_root(directory: Path) -> Path | None:
    for candidate in (directory, *directory.parents):
        if (candidate / "run-info.json").is_file():
            return candidate.resolve()
    return None


_TEXT_FIELDS = PROMPT_FIELDS | {"raw_output", "raw_response", "raw_text", "text", "response"}
_TRACE_FIELDS = {"content_loop_traces", "hierarchy_trace", "loop_trace", "fact_check"}
_LARGE_FIELDS = {"context_packs", "candidate", "parsed_output", "context", "quiz_artifacts"}
_MAX_BLOB_BYTES = 256 * 1024 * 1024


def _reference(target: Path, directory: Path, digest: str, storage: str) -> dict:
    return {"$artifact_ref": {"storage": storage, "sha256": digest,
            "path": Path(os.path.relpath(target, directory)).as_posix()}}


def _archive_fields(value: Any, directory: Path, root: Path, key: str = "") -> Any:
    trace_bytes = len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()) if value else 0
    if key in _TRACE_FIELDS and value and trace_bytes >= 32 * 1024:
        if isinstance(value, list):
            return [_archive_fields(item, directory, root, key) for item in value]
        compact = _archive_fields(value, root / "traces" / key, root)
        data = json.dumps(compact, ensure_ascii=False, sort_keys=True, indent=2).encode()
        digest = hashlib.sha256(data).hexdigest()
        identity = str(value.get("task_id") or value.get("knowledge_unit_id") or "trace") if isinstance(value, dict) else "trace"
        identity = "".join(c if c.isalnum() or c in "-_" else "-" for c in identity)[:64]
        target = root / "traces" / key / f"{identity}-{digest[:16]}.json"
        if not target.exists():
            atomic_json(target, compact)
            # Digest covers the canonical object, independent of JSON whitespace.
        return _reference(target, directory, _object_digest(compact), "json-v1")
    if (key in _TEXT_FIELDS and isinstance(value, str) and len(value) >= 512) or (
        key in _LARGE_FIELDS and len(json.dumps(value, ensure_ascii=False).encode()) >= 4096
    ):
        data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        digest = hashlib.sha256(data).hexdigest()
        target = root / "blobs" / f"{digest}.json.gz"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(gzip.compress(data, mtime=0))
            temporary.replace(target)
        return _reference(target, directory, digest, "json-gzip-v1")
    if isinstance(value, list):
        return [_archive_fields(item, directory, root) for item in value]
    if isinstance(value, dict):
        return {name: _archive_fields(item, directory, root, name) for name, item in value.items()}
    return value


def _object_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _restore_fields(value: Any, directory: Path, active: frozenset[Path] = frozenset()) -> Any:
    if isinstance(value, dict) and set(value) == {"$artifact_ref"}:
        ref = value["$artifact_ref"]
        root = _run_root(directory)
        target = (directory / ref["path"]).resolve()
        if root is None or not target.is_relative_to(root) or target in active:
            raise ValueError("artifact reference escapes run directory or contains cycle")
        if ref["storage"] == "json-gzip-v1":
            if target.parent != root / "blobs" or target.name != f"{ref['sha256']}.json.gz":
                raise ValueError("invalid artifact blob path")
            with gzip.open(target, "rb") as handle:
                data = handle.read(_MAX_BLOB_BYTES + 1)
            if len(data) > _MAX_BLOB_BYTES or hashlib.sha256(data).hexdigest() != ref["sha256"]:
                raise ValueError("artifact blob integrity mismatch or size limit")
            restored = json.loads(data)
        elif ref["storage"] == "json-v1":
            if not target.is_relative_to(root / "traces"):
                raise ValueError("invalid artifact trace path")
            if target.stat().st_size > _MAX_BLOB_BYTES:
                raise ValueError("artifact trace size limit")
            restored = json.loads(target.read_text(encoding="utf-8"))
            if _object_digest(restored) != ref["sha256"]:
                raise ValueError("artifact trace integrity mismatch")
        else:
            raise ValueError("unsupported artifact reference storage")
        return _restore_fields(restored, target.parent, active | {target})
    if isinstance(value, list):
        return [_restore_fields(item, directory, active) for item in value]
    if isinstance(value, dict):
        return {key: _restore_fields(item, directory, active) for key, item in value.items()}
    return value
