from __future__ import annotations

import json
import uuid

from annotation.domain.artifacts import KnowledgeUnit, LearningBlueprint, SourceBlock, SourceDocument
from annotation.providers import ProviderError, SequenceProvider
from annotation.workflow import run_minimal_workflow


def _install_source_fixture(monkeypatch) -> None:
    import annotation.workflow.graph as graph_module

    def parse_fixture(_path, *, run_id: str):
        document = SourceDocument(
            artifact_id=f"source-document-{run_id}",
            run_id=run_id,
            version=1,
            status="draft",
            source_refs=["src-1", "src-2", "src-3"],
            created_by="test",
            title="测试教材",
            locator="fixture://textbook",
        )
        blocks = [
            SourceBlock(
                artifact_id=f"{ref}-artifact",
                source_ref=ref,
                document_id=document.artifact_id,
                run_id=run_id,
                version=1,
                status="draft",
                source_refs=[ref],
                created_by="test",
                page_number=1,
                block_index=index,
                text=f"教材片段 {index}",
                text_hash=f"hash-{index}",
                parser_version="fixture",
                bbox=(0, 0, 1, 1),
            )
            for index, ref in enumerate(document.source_refs)
        ]
        return document, blocks

    monkeypatch.setattr(graph_module, "parse_pdf", parse_fixture)
    monkeypatch.setattr(graph_module, "extraction_warnings", lambda _blocks: [])


def _payload(*, refs: list[str] | None = None, warning: bool = False) -> dict:
    source_refs = ["src-1"] if refs is None else refs
    objective = [] if warning else ["理解教材内容"]
    return {
        "title": "测试蓝图",
        "knowledge_units": [
            {"title": "概念", "kind": "concept", "learning_objectives": objective, "source_refs": source_refs},
            {"title": "定理", "kind": "theorem", "learning_objectives": ["理解定理"], "source_refs": source_refs},
            {"title": "例题", "kind": "example", "learning_objectives": ["完成例题"], "source_refs": source_refs},
        ],
    }


def _run(monkeypatch, provider, *, attempts: int = 3):
    _install_source_fixture(monkeypatch)
    return run_minimal_workflow(
        provider=provider,
        run_id=f"loop-test-{uuid.uuid4().hex[:10]}",
        blueprint_max_attempts=attempts,
    )


def test_first_pass_accepts_with_one_blueprint_call(monkeypatch) -> None:
    provider = SequenceProvider([_payload()])
    state = _run(monkeypatch, provider)

    assert len(provider.calls) == 1
    assert state["workflow_status"] == "accepted"
    trace = state["blueprint_loop_trace"]
    assert trace.final_status == "accepted"
    assert trace.final_attempt == 1
    assert trace.attempts[0].route == "accept"
    assert trace.attempts[0].raw_output


def test_blocking_then_revised_blueprint_uses_back_edge(monkeypatch) -> None:
    provider = SequenceProvider([_payload(refs=[]), _payload()])
    state = _run(monkeypatch, provider)

    assert len(provider.calls) == 2
    assert state["workflow_status"] == "accepted"
    trace = state["blueprint_loop_trace"]
    assert [attempt.route for attempt in trace.attempts] == ["revise", "accept"]
    assert trace.attempts[1].prompt.startswith("# Agent: revise_blueprint")
    assert "blueprint-missing-source" in trace.attempts[1].prompt


def test_three_blocking_attempts_are_blocked_without_document(monkeypatch) -> None:
    provider = SequenceProvider([_payload(refs=[]), _payload(refs=[]), _payload(refs=[])])
    state = _run(monkeypatch, provider)

    assert len(provider.calls) == 3
    assert state["workflow_status"] == "blocked"
    assert "document" not in state
    trace = state["blueprint_loop_trace"]
    assert trace.final_status == "blocked"
    assert trace.stop_reason == "max_attempts"
    assert trace.attempts[-1].route == "block"


def test_warning_only_does_not_revise(monkeypatch) -> None:
    provider = SequenceProvider([_payload(warning=True)])
    state = _run(monkeypatch, provider)

    assert len(provider.calls) == 1
    assert state["workflow_status"] == "accepted"
    assert state["blueprint_loop_trace"].attempts[0].route == "accept"
    assert state["blueprint_loop_trace"].attempts[0].check.issues
    assert all(issue.severity == "warning" for issue in state["blueprint_loop_trace"].attempts[0].check.issues)


def test_real_provider_source_refs_are_not_lexically_repaired(monkeypatch) -> None:
    provider = SequenceProvider([_payload(refs=["not-in-textbook"])])
    state = _run(monkeypatch, provider, attempts=1)

    assert state["workflow_status"] == "blocked"
    assert state["blueprint"].knowledge_units[0].source_refs == ["not-in-textbook"]
    assert state["blueprint_loop_trace"].attempts[0].check.issues[0].category == "source"


def test_schema_and_provider_errors_keep_distinct_terminal_statuses(monkeypatch) -> None:
    invalid_kind = {**_payload(), "knowledge_units": [{"title": "错误", "kind": "unknown"}]}
    schema_state = _run(monkeypatch, SequenceProvider([invalid_kind]), attempts=1)
    schema_attempt = schema_state["blueprint_loop_trace"].attempts[0]
    assert schema_state["workflow_status"] == "blocked"
    assert schema_attempt.generation_status == "schema_error"
    assert schema_attempt.raw_output

    failed_state = _run(
        monkeypatch,
        SequenceProvider([ProviderError("bad credentials", category="authentication")]),
    )
    assert failed_state["workflow_status"] == "failed"
    assert failed_state["blueprint_loop_trace"].final_status == "failed"
    assert failed_state["blueprint_loop_trace"].stop_reason == "non_retryable_provider_error"


def test_retryable_provider_error_can_recover(monkeypatch) -> None:
    provider = SequenceProvider([
        ProviderError("temporary timeout", category="timeout", retryable=True),
        _payload(),
    ])
    state = _run(monkeypatch, provider)

    assert len(provider.calls) == 2
    assert state["workflow_status"] == "accepted"
    assert state["blueprint_loop_trace"].attempts[0].generation_status == "provider_error"
    assert state["blueprint_loop_trace"].attempts[0].route == "revise"


def test_preloaded_blueprint_is_checked_without_model_call(monkeypatch) -> None:
    _install_source_fixture(monkeypatch)
    blueprint = LearningBlueprint(
        artifact_id="preloaded-blueprint",
        run_id="preloaded-run",
        version=1,
        status="accepted",
        source_refs=["src-1"],
        created_by="test",
        title="预加载蓝图",
        knowledge_units=[
            KnowledgeUnit(
                artifact_id=f"preloaded-unit-{index}",
                run_id="preloaded-run",
                version=1,
                status="accepted",
                source_refs=["src-1"],
                created_by="test",
                title=title,
                kind=kind,
                learning_objectives=["目标"],
            )
            for index, (title, kind) in enumerate((("概念", "concept"), ("定理", "theorem"), ("例题", "example")))
        ],
    )
    provider = SequenceProvider([])
    state = run_minimal_workflow(provider=provider, run_id="preloaded-run", blueprint=blueprint)

    assert provider.calls == []
    assert state["blueprint_loop_trace"].attempts == []
    assert state["blueprint_loop_trace"].final_attempt == 0
    assert state["blueprint_loop_trace"].stop_reason == "preloaded"
    assert state["workflow_status"] == "accepted"


def test_trace_artifact_contains_loop_and_run_summary(monkeypatch) -> None:
    provider = SequenceProvider([_payload(refs=[]), _payload()])
    state = _run(monkeypatch, provider)

    blueprint_payload = json.loads(open(state["blueprint_artifact_path"], encoding="utf-8").read())
    run_payload = json.loads(open(state["run_manifest_path"], encoding="utf-8").read())
    assert blueprint_payload["schema_version"] == "blueprint-artifact-v2"
    assert len(blueprint_payload["loop_trace"]["attempts"]) == 2
    assert run_payload["blueprint_loop"]["attempt_count"] == 2
    assert run_payload["blueprint_loop"]["trace_path"] == state["blueprint_trace_path"]
