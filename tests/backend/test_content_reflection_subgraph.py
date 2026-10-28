from __future__ import annotations

from typing import Any

import pytest

from annotation.domain.artifacts import ContentTask, ContextExcerpt, ContextPack, KnowledgeUnit
from annotation.providers.models import (
    ProviderCapabilities,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)
from annotation.workflow.content_reflection import build_content_reflection_subgraph


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
    )
    task = ContentTask(
        depth_guidance="standard",
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
        "attempt": 0,
        "attempts": [],
    }


def _draft(*, refs: list[str]) -> dict[str, Any]:
    return {
        "title": "测试概念",
        "content": "## 讲解\n\n先解释定义，再用 $x^2$ 说明一个教材例子。",
        "callouts": [],
        "source_refs": refs,
    }


def test_split_task_critic_receives_only_its_assigned_objectives():
    import json
    from annotation.domain.artifacts import LearningBlueprint
    from annotation.workflow.course_architect import teaching_blueprint
    state = _state()
    original = type(state["unit"]).model_validate({**state["unit"].model_dump(), "learning_objectives": ["assigned objective", "other task objective"]})
    task = state["task"].model_copy(update={"covered_objectives": {original.artifact_id: [original.objective_ids[0]]}})
    blueprint = LearningBlueprint(artifact_id="bp", run_id=state["run_id"], version=1, status="accepted", created_by="test", title="Book", knowledge_units=[original])
    state["unit"] = teaching_blueprint(blueprint, [task]).knowledge_units[0]
    state["task"] = task
    provider = ScriptedContentProvider([_draft(refs=["src-1"]), {"issues": []}])
    result = build_content_reflection_subgraph(provider, max_attempts=3).invoke(state)
    critic = next(call for call in provider.calls if call.schema.__name__ == "ContentCritiqueDraft")
    context = critic.prompt.split("### Knowledge unit", 1)[1].split("```json", 1)[1].split("```", 1)[0]
    assert json.loads(context)["learning_objectives"] == [original.objective_context()[0]]
    assert "other task objective" not in critic.prompt
    assert "Judge only the learning objectives" in critic.prompt
    assert "### General teaching guidance" in critic.prompt
    assert critic.metadata["prompt_version"] == "critique_content_artifact:v3:optional-subject"
    assert result["route"] == "accept"


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
    assert trace.attempts[0].candidate_artifact.source_refs == ["unknown-source"]
    assert trace.attempts[1].generation_stage == "revision"
    assert trace.attempts[1].generation_prompt.startswith("## Role\n")
    assert "(`revise_content_artifact`)." in trace.attempts[1].generation_prompt.split("## Task", 1)[0]
    assert "invalid-source" in trace.attempts[1].generation_prompt
    assert trace.final_status == "accepted"
    assert len(result["final_artifacts"]) == 1


def test_critic_blocking_finding_routes_to_revision() -> None:
    provider = ScriptedContentProvider([
        _draft(refs=["src-1"]),
        {
            "issues": [
                {
                    "code": "coverage",
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
    assert trace.attempts[1].generation_prompt.startswith("## Role\n")
    assert "(`revise_content_artifact`)." in trace.attempts[1].generation_prompt.split("## Task", 1)[0]
    assert "coverage" in trace.attempts[1].generation_prompt
    assert sum(call.schema.__name__ == "ContentCritiqueDraft" for call in provider.calls) == 2


def test_warning_only_critic_feedback_is_accepted_and_attached_to_final_artifact() -> None:
    provider = ScriptedContentProvider([
        _draft(refs=["src-1"]),
        {
            "issues": [
                {
                    "code": "clarity",
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


@pytest.mark.parametrize("category,retryable,count", [
    ("timeout", True, 3), ("schema", False, 3), ("authentication", False, 1),
])
def test_critic_failure_keeps_candidate_with_warning(category, retryable, count):
    provider = ScriptedContentProvider([
        _draft(refs=["src-1"]),
        *[ProviderError("review unavailable", category=category, retryable=retryable)
          for _ in range(count)],
    ])
    result = build_content_reflection_subgraph(provider, max_attempts=3).invoke(_state())
    assert [c.schema.__name__ for c in provider.calls] == ["ContentDraft"] + ["ContentCritiqueDraft"] * count
    assert result["content_loop_trace"].final_status == "accepted"
    assert len(result["attempts"]) == 1
    trace = result["attempts"][0]
    assert trace.critic_error == "review unavailable"
    assert trace.critic_provider_metadata["critic_attempts"] == count
    artifact = result["final_artifacts"][0]
    assert artifact.content == _draft(refs=["src-1"])["content"]
    assert any(i.severity == "warning" and "教学评审未完成" in i.message for i in artifact.issues)


def test_critic_retry_recovers_without_rewriting_content():
    provider = ScriptedContentProvider([
        _draft(refs=["src-1"]),
        ProviderError("timeout", category="timeout", retryable=True), {"issues": []},
    ])
    result = build_content_reflection_subgraph(provider, max_attempts=3).invoke(_state())
    assert [c.schema.__name__ for c in provider.calls] == ["ContentDraft", "ContentCritiqueDraft", "ContentCritiqueDraft"]
    assert result["route"] == "accept"
    assert not result["final_artifacts"][0].issues


@pytest.mark.parametrize("provider_limit,expected", [(None, 12000), (6000, 6000)])
def test_generation_uses_configured_output_limit(monkeypatch, provider_limit, expected):
    monkeypatch.setenv("MODEL_MAX_OUTPUT_TOKENS", "12000")
    provider = ScriptedContentProvider([_draft(refs=["src-1"]), {"issues": []}])
    from dataclasses import replace
    provider.capabilities = replace(provider.capabilities, max_output_tokens=provider_limit)
    build_content_reflection_subgraph(provider, max_attempts=3).invoke(_state())
    assert provider.calls[0].max_output_tokens == expected


def test_truncation_does_not_retry_same_generation_limit():
    provider = ScriptedContentProvider([ProviderError("truncated", category="truncation", raw_output="partial")])
    result = build_content_reflection_subgraph(provider, max_attempts=3).invoke(_state())
    assert len(provider.calls) == 1
    assert result["attempts"][0].generation_error_category == "truncation"
    assert result["attempts"][0].generation_raw_output == "partial"


@pytest.mark.parametrize("has_hits", [False, True])
def test_agent_can_retrieve_from_empty_bootstrap_but_cannot_generate_without_evidence(has_hits):
    from annotation.domain.artifacts import LearningBlueprint
    from annotation.retrieval import InMemoryTextbookSearchTool
    from test_content_context import _block
    class RetrievalProvider(ScriptedContentProvider):
        provider = "retrieval-fixture"
    state = _state()
    state["context_pack"] = state["context_pack"].model_copy(update={"excerpts": [], "source_refs": [], "selected_source_refs": []})
    state["blueprint"] = LearningBlueprint(artifact_id="blueprint", run_id=state["run_id"], version=1,
        status="accepted", created_by="test", title="教材", knowledge_units=[state["unit"]])
    state["source_blocks"] = [_block("src-1", 1, "教材证据")]
    state["retrieval_service"] = InMemoryTextbookSearchTool(state["source_blocks"])
    provider = RetrievalProvider([
        {"action": "search", "textbook_queries": ["教材证据" if has_hits else "从未出现的查询"]},
        {"action": "write"},
        *([_draft(refs=["src-1"]), {"issues": []}] if has_hits else []),
    ])
    result = build_content_reflection_subgraph(provider, max_attempts=3, retrieval_service=state.pop("retrieval_service", None)).invoke(state)
    trace = result["content_loop_trace"]
    assert trace.final_status == ("accepted" if has_hits else "blocked")
    assert [r.schema.__name__ for r in provider.calls] == (["RetrievalDecision", "RetrievalDecision", "ContentDraft", "ContentCritiqueDraft"] if has_hits else ["RetrievalDecision", "RetrievalDecision"])
    retrieval_prompts = [call.prompt for call in provider.calls if call.schema.__name__ == "RetrievalDecision"]
    assert retrieval_prompts and all("### Retrieval rules" in prompt for prompt in retrieval_prompts)
    if not has_hits:
        assert trace.stop_reason == "context_pack_empty"
        assert not result["context_pack"].source_refs


@pytest.mark.parametrize("supplement", [False, True])
def test_agent_chooses_direct_revision_or_supplement(supplement):
    from annotation.domain.artifacts import LearningBlueprint
    from annotation.retrieval import InMemoryTextbookSearchTool
    from test_content_context import _block
    state = _state()
    state["blueprint"] = LearningBlueprint(artifact_id="bp", run_id=state["run_id"], version=1,
        status="accepted", created_by="test", title="教材", knowledge_units=[state["unit"]])
    state["source_blocks"] = [_block("src-1", 1, "教材证据")]
    class CountingTool(InMemoryTextbookSearchTool):
        def __init__(self, blocks):
            super().__init__(blocks)
            self.queries = []
        def search(self, query, **kwargs):
            self.queries.append(query)
            return super().search(query, **kwargs)
    tool = CountingTool(state["source_blocks"])
    state["retrieval_service"] = tool
    provider = ScriptedContentProvider([
        {"action": "write"}, _draft(refs=["unknown-source"]),
        *([{ "action": "search", "textbook_queries": ["教材证据"]}] if supplement else []),
        {"action": "write"}, _draft(refs=["src-1"]), {"issues": []},
    ])
    result = build_content_reflection_subgraph(provider, max_attempts=3, retrieval_service=state.pop("retrieval_service", None)).invoke(state)
    assert result["content_loop_trace"].final_status == "accepted"
    assert tool.queries == (["教材证据"] if supplement else [])
    decisions = [c for c in provider.calls if c.schema.__name__ == "RetrievalDecision"]
    assert 'unknown-source' in decisions[1].prompt
    assert 'hard_check' in decisions[1].prompt


def test_agent_search_budget_does_not_force_writing():
    from annotation.domain.artifacts import LearningBlueprint
    from annotation.retrieval import InMemoryTextbookSearchTool
    from test_content_context import _block
    state = _state()
    state["blueprint"] = LearningBlueprint(artifact_id="bp", run_id=state["run_id"], version=1,
        status="accepted", created_by="test", title="教材", knowledge_units=[state["unit"]])
    state["source_blocks"] = [_block("src-1", 1, "教材证据")]
    state["retrieval_service"] = InMemoryTextbookSearchTool(state["source_blocks"])
    provider = ScriptedContentProvider([{"action": "search", "textbook_queries": ["教材证据"]}] * 3)
    result = build_content_reflection_subgraph(provider, max_attempts=3, retrieval_service=state.pop("retrieval_service", None)).invoke(state)
    assert result["content_loop_trace"].final_status == "blocked"
    assert all(c.schema.__name__ == "RetrievalDecision" for c in provider.calls)
    assert len(result["context_pack"].retrieval_calls) == 2
    assert result["content_loop_trace"].stop_reason == "retrieval_budget"
