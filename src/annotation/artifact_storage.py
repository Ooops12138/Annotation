"""Stable run identity, readable directories and small developer summaries."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from contextlib import contextmanager
import os

from annotation.config import STORAGE_DIR

_lock = threading.RLock()


@contextmanager
def execution_lock(directory: Path):
    """Exclude archival from live workflow execution across processes."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".execution.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("run execution is active or being archived") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _slug(value: str, limit: int) -> str:
    return re.sub(r'[^\w.-]+', "-", value, flags=re.UNICODE).strip(".-")[:limit] or "run"


def index_path(run_id: str) -> Path:
    return STORAGE_DIR / "artifacts" / ".run-index" / (hashlib.sha256(run_id.encode()).hexdigest() + ".json")


def find_run_directory(run_id: str) -> Path | None:
    pointer = index_path(run_id)
    if not pointer.exists():
        return None
    record = json.loads(pointer.read_text(encoding="utf-8"))
    root = (STORAGE_DIR / "artifacts" / "runs").resolve()
    path = (root / record["directory"]).resolve()
    if record["run_id"] != run_id or path.parent != root:
        raise ValueError("invalid run directory index")
    return path


def run_directory(run_id: str, *, title: str = "", pdf_path: str = "") -> Path:
    if not run_id:
        raise ValueError("run_id is required")
    with _lock:
        directory = find_run_directory(run_id)
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
            return directory
        now = datetime.now(timezone.utc)
        label = title or (Path(pdf_path).stem if pdf_path else "run")
        identity = _slug(run_id, 48) + "-" + hashlib.sha256(run_id.encode()).hexdigest()[:8]
        name = f"{now.astimezone().strftime('%Y%m%d-%H%M%S')}_{_slug(label, 40)}__{identity}"
        directory = STORAGE_DIR / "artifacts" / "runs" / name
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(index_path(run_id), {"run_id": run_id, "directory": name})
        update_run_info(run_id, title=label, pdf_path=pdf_path, created_at=now.isoformat(), status="created")
        return directory


def artifact_directory(run_id: str, kind: str, root: str | Path | None = None) -> Path:
    # Explicit roots retain the public writer contract used by scripts/tests.
    if root is not None:
        return Path(root) / re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip(".-")
    directory = run_directory(run_id)
    if kind == "manifest":
        return directory
    return directory / "results" / kind


def update_run_info(run_id: str, **updates: Any) -> None:
    with _lock:
        directory = find_run_directory(run_id) or run_directory(run_id)
        path = directory / "run-info.json"
        info = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"run_id": run_id}
        info.update(updates)
        info["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json(path, info)
        lines = [f"# {str(info.get('title', '运行')).replace(chr(10), ' ')}", "",
                 f"- 运行 ID：`{run_id}`", f"- 创建时间：{info.get('created_at', '')}",
                 f"- 状态：{info.get('status', '')}", f"- 当前阶段：{info.get('stage', '')}",
                 f"- 模型：{info.get('provider', '')} / {info.get('model', '')}",
                 f"- 教材：{info.get('pdf_path', '')}", "",
                 "结果见 results/，逐任务诊断见 traces/，恢复与调用记录见 execution/。",
                 "大字段引用由 annotation.prompt_archive.read_artifact_json 无损读取。", ""]
        for key, value in (info.get("paths") or {}).items():
            if value:
                target = Path(value)
                try:
                    target = target.relative_to(directory)
                except ValueError:
                    pass
                lines.append(f"- {key}：[{target.as_posix()}](<{target.as_posix()}>)")
        if info.get("errors"):
            lines.extend(["", "错误：", *[f"- {error}" for error in info["errors"]]])
        (directory / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
