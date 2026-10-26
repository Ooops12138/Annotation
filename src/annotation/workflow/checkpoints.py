"""Native LangGraph SQLite persistence with versioned channel payloads."""

from __future__ import annotations

import hashlib
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from annotation.config import STORAGE_DIR


class ChannelPayloadSerializer(JsonPlusSerializer):
    """Store each native channel version once, rather than rewriting source blocks.

    SqliteSaver still owns checkpoint IDs, pending writes and scheduling. Only
    its serialization boundary replaces channel values with local blob refs.
    """

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self._lock = threading.RLock()
        self._references: dict[tuple[str, str], str] = {}

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        if (
            not isinstance(obj, dict)
            or "channel_versions" not in obj
            or "channel_values" not in obj
        ):
            return super().dumps_typed(obj)
        values = {}
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            for key, value in obj["channel_values"].items():
                version = obj["channel_versions"].get(key)
                # Unversioned scheduling values retain normal native serialization.
                if version is None:
                    values[key] = value
                    continue
                cache_key = (key, str(version))
                digest = self._references.get(cache_key)
                if digest is None:
                    kind, data = super().dumps_typed(value)
                    payload = kind.encode("ascii") + b"\n" + data
                    digest = hashlib.sha256(payload).hexdigest()
                    target = self.root / digest
                    if not target.exists():
                        temporary = self.root / f".{uuid.uuid4().hex}.tmp"
                        temporary.write_bytes(payload)
                        temporary.replace(target)
                    self._references[cache_key] = digest
                values[key] = digest
        return super().dumps_typed(
            {**obj, "channel_values": values, "annotation_channel_refs": True}
        )

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        obj = super().loads_typed(data)
        if not isinstance(obj, dict) or not obj.pop("annotation_channel_refs", False):
            return obj
        values = {}
        for key, value in obj["channel_values"].items():
            if key not in obj["channel_versions"]:
                values[key] = value
                continue
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError("invalid checkpoint channel reference")
            payload = (self.root / value).read_bytes()
            if hashlib.sha256(payload).hexdigest() != value:
                raise ValueError("checkpoint channel integrity mismatch")
            kind, raw = payload.split(b"\n", 1)
            values[key] = super().loads_typed((kind.decode("ascii"), raw))
            self._references[(key, str(obj["channel_versions"][key]))] = value
        return {**obj, "channel_values": values}


def checkpoint_directory(run_id: str, root: Path | None = None) -> Path:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be a single path component")
    return (root or STORAGE_DIR / "artifacts" / "execution") / run_id


@contextmanager
def workflow_checkpointer(
    run_id: str, root: Path | None = None
) -> Iterator[SqliteSaver]:
    directory = checkpoint_directory(run_id, root)
    directory.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(directory / "checkpoints.sqlite3")) as saver:
        saver.serde = ChannelPayloadSerializer(directory / "channels")
        yield saver


def resumable_checkpoint(
    run_id: str, root: Path | None = None
) -> dict[str, Any] | None:
    directory = checkpoint_directory(run_id, root)
    if not (directory / "checkpoints.sqlite3").is_file():
        return None
    with workflow_checkpointer(run_id, root) as saver:
        saved = next(saver.list(None, limit=1), None)
        if saved is None:
            return None
        return {"run_id": run_id, "config": saved.config}
