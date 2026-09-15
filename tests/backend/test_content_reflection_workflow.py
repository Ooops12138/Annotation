from __future__ import annotations

import uuid
from typing import Any

from annotation.domain.artifacts import KnowledgeUnit, LearningBlueprint, SourceBlock, SourceDocument
from annotation.providers.models import (
    ProviderCapabilities,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)
from annotation.providers import MockProvider
from annotation.workflow import run_minimal_workflow


class WorkflowContentProvider:
    provider = "workflow-scripted"
    model = "workflow-scripted-model"
    base_url = None
    config_version = "test-v1"

    def __init__(self, *, mode: str, quiz_unit_ids: list[str], context_window: int = 12000) -> None:
        self.mode = mode
        self.quiz_unit_ids = list(quiz_unit_ids)
        self.calls: list[StructuredGenerationRequest[Any]] = []
        self.capabilities = ProviderCapabilities(
            supports_structured_output=True,
            context_window=context_window,
        )

    def generate_structured(self, request: StructuredGenerationRequest[Any]) -> StructuredGenerationResponse[Any]:
        self.calls.append(request)
        schema_name = request.schema.__name__
        if schema_name == "ContentDraft":
            payload: dict[str, Any] = {
                "title": "测试单元",
                "content": "先解释定义，再用 $x^2$ 说明一个教材例子。",
                "material_role": "explanation",
                "teaching_material": "跟做：按教材步骤代入一个例子，并逐步说明。",
                "source_refs": ["unknown-source"] if self.mode == "blocking" else ["src-1"],
            }
        elif schema_name == "ContentCritiqueDraft":
            if self.mode == "blocking":
                raise AssertionError("hard-check failures must not call the Critic")
            payload = {
                "issues": [
                    {
                        "code": "beginner_clarity",
                        "target": "content",
                        "message": "需要先解释符号含义。",
                    }
                ] if not any(call.schema.__name__ == "ContentCritiqueDraft" for call in self.calls[:-1]) else []
            }
        elif schema_name == "QuizDraft":
            payload = {
                "knowledge_unit_id": self.quiz_unit_ids.pop(0),
                "question_count": 0,
                "target_objectives": [],
                "questions": [],
            }
        else:  # pragma: no cover - makes a new workflow call visible in the test.
            raise AssertionError(f"unexpected schema: {schema_name}")
        value = request.schema.model_validate(payload)
        return StructuredGenerationResponse(
            value=value,
            raw_text=value.model_dump_json(),
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            config_version=self.config_version,
            duration_ms=0,
            usage={},
        )


def _install_source_fixture(monkeypatch) -> None:
    import annotation.workflow.graph as graph_module

    def parse_fixture(_path, *, run_id: str):
        document = SourceDocument(
            artifact_id=f"source-document-{run_id}",
            run_id=run_id,
            version=1,
            status="draft",
            source_refs=["src-1"],
            created_by="test",
            title="测试教材",
            locator="fixture://textbook",
        )
        block = SourceBlock(
            artifact_id=f"src-1-{run_id}",
            source_ref="src-1",
            document_id=document.artifact_id,
            run_id=run_id,
            version=1,
            status="draft",
            source_refs=["src-1"],
            created_by="test",
            page_number=1,
            block_index=0,
            text="测试教材证据。",
            text_hash="source-hash",
            parser_version="fixture",
            bbox=(0, 0, 1, 1),
        )
        return document, [block]

    monkeypatch.setattr(graph_module, "parse_pdf", parse_fixture)
    monkeypatch.setattr(graph_module, "extraction_warnings", lambda _blocks: [])


def _blueprint(run_id: str) -> LearningBlueprint:
    units = [
        KnowledgeUnit(
            artifact_id=f"unit-{index}",
            run_id=run_id,
            version=1,
            status="accepted",
            source_refs=["src-1"],
            created_by="test",
            title=title,
            kind=kind,
            learning_objectives=[f"理解{title}"],
            teaching_materials=["跟做材料"],
        )
        for index, (title, kind) in enumerate(
            (("概念", "concept"), ("定理", "theorem"), ("例题", "example")),
            start=1,
        )
    ]
    return LearningBlueprint(
        artifact_id=f"blueprint-{run_id}",
        run_id=run_id,
        version=1,
        status="accepted",
        source_refs=["src-1"],
        created_by="test",
        title="测试章节",
        knowledge_units=units,
    )


def test_critic_warning_reaches_review_report_and_keeps_accepted_preview(monkeypatch) -> None:
    _install_source_fixture(monkeypatch)
    run_id = f"content-warning-{uuid.uuid4().hex[:8]}"
    blueprint = _blueprint(run_id)
    provider = WorkflowContentProvider(
        mode="warning",
        quiz_unit_ids=[unit.artifact_id for unit in blueprint.knowledge_units],
    )

    state = run_minimal_workflow(provider=provider, run_id=run_id, blueprint=blueprint)

    assert state["workflow_status"] == "accepted"
    assert state["content_loop_status"] == "accepted"
    assert len(state["content_loop_traces"]) == 3
    assert all(trace.final_status == "accepted" for trace in state["content_loop_traces"])
    assert any(
        issue.message == "需要先解释符号含义。" and issue.severity == "warning"
        for issue in state["review_report"].issues
    )
    assert state["review_report"].status == "at_risk"
    assert state["document"].status == "accepted"


def test_exhausted_content_loop_blocks_run_and_skips_quiz_and_document(monkeypatch) -> None:
    _install_source_fixture(monkeypatch)
    run_id = f"content-blocked-{uuid.uuid4().hex[:8]}"
    blueprint = _blueprint(run_id)
    provider = WorkflowContentProvider(
        mode="blocking",
        quiz_unit_ids=[unit.artifact_id for unit in blueprint.knowledge_units],
    )

    state = run_minimal_workflow(
        provider=provider,
        run_id=run_id,
        blueprint=blueprint,
        content_reflection_max_attempts=3,
    )

    assert state["workflow_status"] == "blocked"
    assert state["content_loop_status"] == "blocked"
    assert "document" not in state
    assert "quiz_artifacts" not in state
    assert len(state["content_loop_traces"]) == 3
    assert all(trace.final_status == "blocked" for trace in state["content_loop_traces"])
    assert all(len(trace.attempts) == 3 for trace in state["content_loop_traces"])
    assert all(attempt.critic_status == "skipped" for trace in state["content_loop_traces"] for attempt in trace.attempts)
    assert all(call.schema.__name__ == "ContentDraft" for call in provider.calls)


def test_context_pack_over_budget_creates_attempt_zero_without_model_calls(monkeypatch) -> None:
    _install_source_fixture(monkeypatch)
    run_id = f"content-preflight-{uuid.uuid4().hex[:8]}"
    blueprint = _blueprint(run_id)
    provider = WorkflowContentProvider(
        mode="warning",
        quiz_unit_ids=[unit.artifact_id for unit in blueprint.knowledge_units],
        context_window=1,
    )

    state = run_minimal_workflow(provider=provider, run_id=run_id, blueprint=blueprint)

    assert state["workflow_status"] == "blocked"
    assert state["content_loop_status"] == "blocked"
    assert provider.calls == []
    assert all(trace.final_status == "blocked" for trace in state["content_loop_traces"])
    assert all(trace.final_attempt == 0 for trace in state["content_loop_traces"])
    assert all(trace.stop_reason == "context_pack_source_over_budget" for trace in state["content_loop_traces"])
    assert all(trace.attempts[0].generation_status == "skipped" for trace in state["content_loop_traces"])


def test_mock_workflow_keeps_six_unit_quiz_regression_and_emits_six_content_traces() -> None:
    state = run_minimal_workflow(
        provider=MockProvider(),
        run_id=f"content-mock-regression-{uuid.uuid4().hex[:8]}",
    )

    assert len(state["blueprint"].knowledge_units) == 6
    assert len(state["content_loop_traces"]) == 6
    assert all(trace.final_status == "accepted" for trace in state["content_loop_traces"])
    assert all(len(trace.attempts) == 1 for trace in state["content_loop_traces"])
    assert all(trace.attempts[0].critic_status == "succeeded" for trace in state["content_loop_traces"])
    assert state["content_loop_summary"]["unit_count"] == 6
    assert state["content_loop_summary"]["critic_call_count"] == 6
    assert len(state["quiz_artifacts"]) == 6
