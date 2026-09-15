from __future__ import annotations

from typing import Any

from annotation.domain.artifacts import ContentTask, ContextExcerpt, ContextPack, KnowledgeUnit
from annotation.providers.models import (
    ProviderCapabilities,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)
from annotation.workflow.graph import build_content_reflection_subgraph


class ScriptedContentProvider:
    """Replay structured generation by schema without using Mock adaptation."""

    provider = "scripted"
    model = "scripted-content-model"
    base_url = None
    config_version = "test-v1"
    capabilities = ProviderCapabilities(supports_structured_output=True)

    def __init__(self, responses: list[dict[str, Any] | ProviderError]) -> None:
        self.responses = list(responses)
        self.calls: list[StructuredGenerationRequest[Any]] = []

    def generate_structured(self, request: StructuredGenerationRequest[Any]) -> StructuredGenerationResponse[Any]:
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, ProviderError):
            raise response
        value = request.schema.model_validate(response)
        return StructuredGenerationResponse(
            value=value,
            raw_text=value.model_dump_json(),
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            config_version=self.config_version,
            duration_ms=0,
            usage={"total_tokens": 1},
        )


def _state() -> dict[str, Any]:
    run_id = "run-content-subgraph"
    unit = KnowledgeUnit(
        artifact_id="unit-content-subgraph",
        run_id=run_id,
        version=1,
        status="accepted",
        source_refs=["src-1"],
        created_by="test",
        title="测试概念",
        kind="concept",
        learning_objectives=["理解测试概念"],
        teaching_materials=["跟做材料"],
    )
    task = ContentTask(
        task_id="task-content-subgraph",
        run_id=run_id,
        blueprint_version="blueprint:v1",
        knowledge_unit_id=unit.artifact_id,
        source_refs=["src-1"],
        acceptance_criteria=["覆盖学习目标"],
    )
    pack = ContextPack(
        context_pack_id="ctx-content-subgraph",
        run_id=run_id,
        task_id=task.task_id,
        knowledge_unit_id=unit.artifact_id,
        source_refs=["src-1"],
        selected_source_refs=["src-1"],
        excerpts=[ContextExcerpt(source_ref="src-1", page_number=1, block_index=0, text="教材证据")],
        estimated_input_tokens=2,
        input_budget_tokens=100,
    )
    return {
        "run_id": run_id,
        "task": task,
        "unit": unit,
        "context_pack": pack,
        "valid_source_refs": ["src-1"],
        "max_attempts": 3,
        "fixture_adaptation": False,
        "attempt": 0,
        "attempts": [],
    }


def _draft(*, refs: list[str]) -> dict[str, Any]:
    return {
        "title": "测试概念",
        "content": "先解释定义，再用 $x^2$ 说明一个教材例子。",
        "material_role": "explanation",
        "teaching_material": "跟做：按教材步骤代入一个例子，并逐步说明。",
        "source_refs": refs,
    }


def test_hard_check_revises_before_calling_critic_and_preserves_source_evidence() -> None:
    provider = ScriptedContentProvider([
        _draft(refs=["unknown-source"]),
        _draft(refs=["src-1"]),
        {"issues": []},
    ])

    result = build_content_reflection_subgraph(provider, max_attempts=3).invoke(_state())

    trace = result["content_loop_trace"]
    assert [call.schema.__name__ for call in provider.calls] == [
        "ContentDraft",
        "ContentDraft",
        "ContentCritiqueDraft",
    ]
    assert [attempt.route for attempt in trace.attempts] == ["revise", "accept"]
    assert trace.attempts[0].critic_status == "skipped"
    assert trace.attempts[0].candidate_artifacts[0].source_refs == ["unknown-source"]
    assert trace.attempts[1].generation_stage == "revision"
    assert trace.attempts[1].generation_prompt.startswith("# Agent: revise_content_artifact")
    assert "invalid-source" in trace.attempts[1].generation_prompt
    assert trace.final_status == "accepted"


def test_critic_blocking_finding_routes_to_revision() -> None:
    provider = ScriptedContentProvider([
        _draft(refs=["src-1"]),
        {
            "issues": [
                {
                    "code": "objective_missing",
                    "target": "content",
                    "message": "没有解释学习目标。",
                    "suggested_action": "补上目标关联。",
                }
            ]
        },
        _draft(refs=["src-1"]),
        {"issues": []},
    ])

    result = build_content_reflection_subgraph(provider, max_attempts=3).invoke(_state())

    trace = result["content_loop_trace"]
    assert [attempt.route for attempt in trace.attempts] == ["revise", "accept"]
    assert trace.attempts[0].feedback[0].severity == "blocking"
    assert trace.attempts[1].generation_prompt.startswith("# Agent: revise_content_artifact")
    assert "objective_missing" in trace.attempts[1].generation_prompt
    assert sum(call.schema.__name__ == "ContentCritiqueDraft" for call in provider.calls) == 2


def test_warning_only_critic_feedback_is_accepted_and_attached_to_final_artifact() -> None:
    provider = ScriptedContentProvider([
        _draft(refs=["src-1"]),
        {
            "issues": [
                {
                    "code": "beginner_clarity",
                    "target": "content",
                    "message": "符号说明还可以更清楚。",
                }
            ]
        },
    ])

    result = build_content_reflection_subgraph(provider, max_attempts=3).invoke(_state())

    trace = result["content_loop_trace"]
    primary = result["final_artifacts"][0]
    assert len(trace.attempts) == 1
    assert trace.attempts[0].route == "accept"
    assert trace.attempts[0].feedback[0].severity == "warning"
    assert primary.status == "accepted"
    assert any(issue.severity == "warning" for issue in primary.issues)


def test_three_hard_check_failures_block_without_any_critic_call() -> None:
    provider = ScriptedContentProvider([
        _draft(refs=["unknown-source"]),
        _draft(refs=["unknown-source"]),
        _draft(refs=["unknown-source"]),
    ])

    result = build_content_reflection_subgraph(provider, max_attempts=3).invoke(_state())

    trace = result["content_loop_trace"]
    assert trace.final_status == "blocked"
    assert trace.stop_reason == "max_attempts"
    assert [attempt.route for attempt in trace.attempts] == ["revise", "revise", "block"]
    assert all(attempt.critic_status == "skipped" for attempt in trace.attempts)
    assert [call.schema.__name__ for call in provider.calls] == ["ContentDraft"] * 3
    assert all(artifact.status == "blocked" for artifact in result["final_artifacts"])


def test_non_retryable_provider_error_fails_without_retrying_the_unit() -> None:
    provider = ScriptedContentProvider([
        ProviderError("invalid credentials", category="authentication"),
    ])

    result = build_content_reflection_subgraph(provider, max_attempts=3).invoke(_state())

    trace = result["content_loop_trace"]
    assert trace.final_status == "failed"
    assert trace.stop_reason == "non_retryable_provider_error"
    assert len(trace.attempts) == 1
    assert trace.attempts[0].route == "fail"
    assert trace.attempts[0].generation_error_category == "authentication"
    assert result["final_artifacts"][0].status == "blocked"
