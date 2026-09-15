from __future__ import annotations

import json

from annotation.api.routes import _run_response_from_state
from annotation.domain.artifacts import ContentAttemptTrace, ContentUnitLoopTrace


def test_fresh_run_response_exposes_content_loop_summary_without_trace_payload() -> None:
    trace = ContentUnitLoopTrace(
        trace_id="content-loop-unit-1",
        run_id="run-content-api",
        task_id="task-unit-1",
        knowledge_unit_id="unit-1",
        context_pack_id="ctx-task-unit-1",
        max_attempts=3,
        attempts=[
            ContentAttemptTrace(
                attempt=1,
                generation_stage="initial",
                generation_prompt="private generation prompt",
                generation_raw_output='{"content":"private raw output"}',
                generation_status="succeeded",
                critic_prompt="private critic prompt",
                critic_raw_output='{"issues":[]}',
                critic_status="succeeded",
                route="accept",
                stop_reason="accepted",
            )
        ],
        final_status="accepted",
        final_attempt=1,
        stop_reason="accepted",
    )

    payload = _run_response_from_state(
        {"run": {"status": "succeeded"}},
        {
            "content_loop_traces": [trace],
            "content_loop_trace_path": "C:/artifacts/content/run-content-api/content.json",
        },
    )

    assert payload["content_loop"] == {
        "unit_count": 1,
        "accepted_count": 1,
        "blocked_count": 0,
        "failed_count": 0,
        "attempt_count": 1,
        "final_status": "accepted",
        "trace_path": "C:/artifacts/content/run-content-api/content.json",
    }
    serialized_summary = json.dumps(payload["content_loop"], ensure_ascii=False)
    assert "generation_prompt" not in serialized_summary
    assert "generation_raw_output" not in serialized_summary
    assert "critic_prompt" not in serialized_summary
    assert "attempts" not in serialized_summary
