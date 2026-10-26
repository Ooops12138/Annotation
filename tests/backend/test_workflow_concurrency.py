from collections import Counter
from threading import Barrier, Lock
from types import SimpleNamespace
import uuid

import pytest

from annotation.offline import OfflineFixtureProvider
from annotation.workflow import run_minimal_workflow
from annotation.workflow import runtime, fact_check
from annotation.workflow.concurrency import ordered_batches, workflow_max_workers


@pytest.mark.parametrize("workers", [1, 2])
def test_three_stages_overlap_tasks_and_keep_output_order(monkeypatch, workers):
    monkeypatch.setenv("WORKFLOW_MAX_WORKERS", str(workers))
    monkeypatch.setenv("RAG_ENABLED", "false")
    active, peak, calls = Counter(), Counter(), Counter()
    lock = Lock()
    barriers = {stage: Barrier(workers) for stage in ("content", "quiz", "fact")}

    def observe(stage, function):
        def wrapped(*args, **kwargs):
            with lock:
                assert not any(value for key, value in active.items() if key != stage)
                active[stage] += 1
                peak[stage] = max(peak[stage], active[stage])
                calls[stage] += 1
            try:
                # A serial implementation cannot pass this barrier.
                barriers[stage].wait(timeout=15)
                return function(*args, **kwargs)
            finally:
                with lock:
                    active[stage] -= 1
        return wrapped

    build = runtime.build_content_reflection_subgraph

    def tracked_build(*args, **kwargs):
        graph = build(*args, **kwargs)
        return SimpleNamespace(invoke=observe("content", graph.invoke))

    monkeypatch.setattr(runtime, "build_content_reflection_subgraph", tracked_build)
    monkeypatch.setattr(runtime, "generate_quiz_artifact", observe("quiz", runtime.generate_quiz_artifact))
    monkeypatch.setattr(fact_check, "_check_unit", observe("fact", fact_check._check_unit))
    result = run_minimal_workflow(provider=OfflineFixtureProvider(), run_id=f"parallel-{uuid.uuid4().hex[:8]}")
    task_ids = [task.task_id for task in result["content_tasks"]]
    assert calls == {"content": 6, "quiz": 6, "fact": 6}
    assert peak == {"content": workers, "quiz": workers, "fact": workers}
    assert [item.task_id for item in result["content_artifacts"]] == task_ids
    assert [item.task_id for item in result["quiz_artifacts"]] == task_ids
    assert [item.task_id for item in result["fact_check_artifact"].unit_traces] == task_ids
    assert result["document"].sections
    assert result["content_retrieval_connection"] is None


def test_failed_batch_preserves_started_tasks_and_skips_later_tasks(monkeypatch):
    monkeypatch.setenv("WORKFLOW_MAX_WORKERS", "2")
    monkeypatch.setenv("RAG_ENABLED", "false")
    barrier = Barrier(2)
    invoked = []
    build = runtime.build_content_reflection_subgraph

    def tracked_build(*args, **kwargs):
        graph = build(*args, **kwargs)

        def invoke(state):
            invoked.append(state["task"].task_id)
            barrier.wait(timeout=15)
            result = graph.invoke(state)
            if state["task"].task_id.endswith("001"):
                result["content_loop_trace"].final_status = "failed"
                result["content_loop_trace"].stop_reason = "non_retryable_provider_error"
            return result

        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(runtime, "build_content_reflection_subgraph", tracked_build)
    result = run_minimal_workflow(provider=OfflineFixtureProvider(), run_id=f"parallel-fail-{uuid.uuid4().hex[:8]}")
    assert len(invoked) == 2
    assert [item.final_status for item in result["content_loop_traces"]] == ["failed", "accepted", *["skipped"] * 4]
    assert result["workflow_status"] == "failed"
    assert not result.get("document")
    assert result["content_retrieval_connection"] is None


def test_serial_mode_stops_without_dispatching_the_next_item():
    calls = []

    def work(item):
        calls.append(item)
        return item

    assert ordered_batches(work, range(4), workers=1, stop=lambda value: value == 1) == [0, 1]
    assert calls == [0, 1]


@pytest.mark.parametrize("value", ["0", "9", "invalid"])
def test_invalid_worker_limit_fails_before_generation(monkeypatch, value):
    monkeypatch.setenv("WORKFLOW_MAX_WORKERS", value)
    with pytest.raises(ValueError):
        workflow_max_workers()
