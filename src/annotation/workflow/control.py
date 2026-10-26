"""Cooperative execution and durable audit checkpoints for local runs."""
from __future__ import annotations
import json
import threading
import uuid
from pathlib import Path
from annotation.config import STORAGE_DIR

class RunStopped(BaseException):
    """Control flow: must bypass broad model/tool exception handlers."""

class RunControl:
    def __init__(self, run_id: str = "", root: Path | None = None):
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self.committing = False
        self.run_id = run_id
        self.root = root or STORAGE_DIR / "artifacts" / "execution"
        self.sequence = 0

    @property
    def stop_requested(self):
        return self._stop.is_set()

    def request_stop(self):
        with self.lock:
            if self.committing:
                return False
            changed = not self._stop.is_set()
            self._stop.set()
            return changed

    def raise_if_stopped(self):
        if self.stop_requested:
            raise RunStopped("user_requested_stop")

    def checkpoint(self, kind, value):
        if not self.run_id:
            return
        from annotation.workflow.persistence import _model_dump
        with self.lock:
            self.sequence += 1
            directory = self.root / self.run_id
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{self.sequence:06d}.json"
            temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            payload = {"run_id": self.run_id, "kind": kind, "value": _model_dump(value)}
            temporary.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
            temporary.replace(path)

class ControlledPort:
    """Guard every model/tool operation, preserving completed responses first."""
    def __init__(self, target, control):
        self._target, self._control = target, control

    def __getattr__(self, name):
        value = getattr(self._target, name)
        if name not in {"generate", "generate_structured", "search", "stream"} or not callable(value):
            return value
        def call(*args, **kwargs):
            self._control.raise_if_stopped()
            result = value(*args, **kwargs)
            self._control.checkpoint(name, {"request": args, "kwargs": kwargs, "response": result})
            self._control.raise_if_stopped()
            return result
        return call
