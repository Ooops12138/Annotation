"""Local JSONL logging for model requests and responses."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from annotation.config import STORAGE_DIR


_LOCK = threading.Lock()


def log_model_call(
    *,
    provider: str,
    model: str,
    base_url: str | None,
    request_type: str,
    metadata: Mapping[str, Any],
    prompt: str,
    system_prompt: str | None,
    max_output_tokens: int | None,
    temperature: float | None,
    output: str = "",
    parsed_output: Any | None = None,
    reasoning_output: str | None = None,
    usage: Mapping[str, int] | None = None,
    duration_ms: int | None = None,
    finish_reason: str | None = None,
    thinking: str | None = None,
    status: str = "success",
    error: str | None = None,
) -> None:
    """Append one complete provider interaction to a local JSONL file.

    The request payload is intentionally limited to model-call data. API keys
    are never passed to this function and therefore never written to the log.
    """
    configured_path = os.getenv("MODEL_CALL_LOG_PATH")
    path = Path(configured_path) if configured_path else STORAGE_DIR / "model-calls.jsonl"
    record = {
        "call_id": f"call-{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "request_type": request_type,
        "metadata": dict(metadata),
        "input": {
            "system_prompt": system_prompt,
            "prompt": prompt,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "thinking": thinking,
        },
        "output": output,
        "reasoning_output": reasoning_output,
        "parsed_output": parsed_output,
        "usage": dict(usage or {}),
        "duration_ms": duration_ms,
        "finish_reason": finish_reason,
        "status": status,
        "error": error,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
