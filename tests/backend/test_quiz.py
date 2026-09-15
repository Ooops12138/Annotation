import pytest
from pydantic import ValidationError

from annotation.domain.artifacts import (
    ContextExcerpt,
    ContextPack,
    ContentTask,
    KnowledgeUnit,
    LearningBlueprint,
    QuizDraft,
    QuizArtifact,
    QuizQuestion,
)
from annotation.providers import MockProvider, ProviderError
from annotation.workflow.quiz import (
    build_quiz_coverage_matrix,
    build_quiz_prompt,
    generate_quiz_artifact,
    validate_quiz_artifact,
    validate_quiz_coverage,
)


def _unit(unit_id: str = "ku-1") -> KnowledgeUnit:
    return KnowledgeUnit(
        artifact_id=unit_id,
        run_id="run-quiz",
        version=1,
        status="accepted",
        created_by="test",
        title="上确界",
        kind="concept",
        learning_objectives=["区分最大元与上确界", "理解完全公理的作用"],
        source_refs=["src-1"],
    )


def _task(unit: KnowledgeUnit, *, quiz_count: int | None = None) -> ContentTask:
    return ContentTask(
        task_id=f"task-{unit.artifact_id}",
        run_id="run-quiz",
        blueprint_version="bp-quiz:v1",
        knowledge_unit_id=unit.artifact_id,
        content_types=["quiz"],
        quiz_count=quiz_count,
        source_refs=["src-1"],
    )


def _pack(unit: KnowledgeUnit, *, refs: list[str] | None = None) -> ContextPack:
    refs = refs or ["src-1"]
    return ContextPack(
        context_pack_id=f"ctx-{unit.artifact_id}",
        run_id="run-quiz",
        task_id=f"task-{unit.artifact_id}",
        knowledge_unit_id=unit.artifact_id,
        source_refs=refs,
        selected_source_refs=refs,
        excerpts=[ContextExcerpt(source_ref=refs[0], page_number=1, block_index=0, text="上确界是所有上界中最小的上界。")],
        estimated_input_tokens=20,
        input_budget_tokens=100,
    )


def _question(unit: KnowledgeUnit, question_id: str, *, source_refs: list[str] | None = None) -> QuizQuestion:
    return QuizQuestion(
        question_id=question_id,
        knowledge_unit_id=unit.artifact_id,
        target_objectives=[unit.learning_objectives[0]],
        question="下列哪项说法正确？",
        options=["教材支持的说法", "未被教材支持的说法"],
        answer="教材支持的说法",
        explanation="教材片段直接支持答案。",
        source_refs=source_refs or ["src-1"],
    )


def _artifact(unit: KnowledgeUnit, *, questions: list[QuizQuestion] | None = None, source_refs: list[str] | None = None) -> QuizArtifact:
    question_items = (
        [_question(unit, f"{unit.artifact_id}-q1"), _question(unit, f"{unit.artifact_id}-q2")]
        if questions is None
        else questions
    )
    return QuizArtifact(
        artifact_id=f"quiz-{unit.artifact_id}",
        run_id="run-quiz",
        version=1,
        status="draft",
        created_by="test",
        source_refs=source_refs or ["src-1"],
        knowledge_unit_ids=[unit.artifact_id],
        target_objectives=list(unit.learning_objectives),
        questions=question_items,
        task_id=f"task-{unit.artifact_id}",
        context_pack_id=f"ctx-{unit.artifact_id}",
        prompt_version="generate_quiz_artifact:v2",
    )


def test_question_model_rejects_duplicate_options_and_non_option_answer() -> None:
    with pytest.raises(ValidationError):
        QuizQuestion(
            question_id="q-bad",
            knowledge_unit_id="ku-1",
            target_objectives=["目标"],
            question="题目",
            options=["A", "A"],
            answer="A",
            explanation="解析",
            source_refs=["src-1"],
        )
    with pytest.raises(ValidationError):
        QuizQuestion(
            question_id="q-bad-answer",
            knowledge_unit_id="ku-1",
            target_objectives=["目标"],
            question="题目",
            options=["A", "B"],
            answer="C",
            explanation="解析",
            source_refs=["src-1"],
        )


def test_quiz_count_is_an_optional_hint_and_never_a_positive_minimum() -> None:
    unit = _unit()
    assert _task(unit).quiz_count is None
    assert _task(unit, quiz_count=0).quiz_count == 0
    with pytest.raises(ValidationError):
        _task(unit, quiz_count=-1)
    with pytest.raises(ValidationError):
        QuizDraft(knowledge_unit_id=unit.artifact_id, question_count=1, questions=[])


def test_mock_generation_is_deterministic_and_does_not_call_provider() -> None:
    class NoCallMock(MockProvider):
        def generate_structured(self, request):  # pragma: no cover - failure proves the branch
            raise AssertionError("mock quiz generation must stay local")

    unit = _unit()
    artifact = generate_quiz_artifact(NoCallMock(), task=_task(unit), unit=unit, context_pack=_pack(unit))
    assert artifact.status == "accepted"
    assert artifact.artifact_id == "quiz-task-ku-1"
    assert len(artifact.questions) == 2
    assert all(question.source_refs == ["src-1"] for question in artifact.questions)
    assert artifact.questions[0].model_dump() == generate_quiz_artifact(
        NoCallMock(), task=_task(unit), unit=unit, context_pack=_pack(unit)
    ).questions[0].model_dump()


def test_agent_selected_zero_questions_is_a_valid_empty_quiz_artifact() -> None:
    unit = _unit()
    artifact = generate_quiz_artifact(
        MockProvider(),
        task=_task(unit, quiz_count=0),
        unit=unit,
        context_pack=_pack(unit),
    )
    assert artifact.status == "accepted"
    assert artifact.questions == []
    assert artifact.question_count == 0
    assert artifact.generation_metadata["question_count"] == 0


def test_validation_blocks_missing_sources_and_invalid_objective_mapping() -> None:
    unit = _unit()
    bad_question = _question(unit, "q-invalid", source_refs=["src-missing"]).model_copy(
        update={"target_objectives": ["not-an-objective"]}
    )
    artifact = _artifact(unit, questions=[bad_question, _question(unit, "q-valid")])
    check = validate_quiz_artifact(artifact, unit=unit, valid_source_refs={"src-1"})
    assert check.status == "blocked"
    assert any(issue.category == "source" and issue.severity == "blocking" for issue in check.issues)
    assert any(issue.category == "coverage" and issue.severity == "blocking" for issue in check.issues)


def test_validation_limits_question_sources_to_context_pack() -> None:
    unit = _unit()
    artifact = _artifact(unit, questions=[
        _question(unit, "q-context-1", source_refs=["src-2"]),
        _question(unit, "q-context-2", source_refs=["src-2"]),
    ])
    check = validate_quiz_artifact(
        artifact,
        unit=unit,
        context_pack=_pack(unit, refs=["src-1"]),
        valid_source_refs={"src-1", "src-2"},
    )
    assert check.status == "blocked"
    assert any(issue.issue_id.startswith("quiz-question-invalid-sources") for issue in check.issues)


def test_coverage_matrix_accepts_agent_selected_counts_and_records_objective_gaps() -> None:
    unit_one = _unit("ku-1")
    unit_two = _unit("ku-2")
    artifact = _artifact(unit_one, questions=[_question(unit_one, "ku-1-q1")])
    blueprint = LearningBlueprint(
        artifact_id="bp-quiz",
        run_id="run-quiz",
        version=1,
        status="accepted",
        created_by="test",
        title="测验测试",
        source_refs=["src-1"],
        knowledge_units=[unit_one, unit_two],
    )
    report = validate_quiz_coverage([artifact], blueprint, valid_source_refs={"src-1"})
    assert report.status == "passed"
    assert set(report.missing_unit_ids) == {"ku-2"}
    assert not any(issue.issue_id == "quiz-unit-count-ku-1" for issue in report.issues)
    assert any(issue.issue_id == "quiz-missing-unit-ku-2" and issue.severity == "warning" for issue in report.issues)
    assert any(issue.issue_id == "quiz-objective-gap-ku-1" and issue.severity == "warning" for issue in report.issues)
    matrix = build_quiz_coverage_matrix([artifact])
    assert matrix["ku-1"][0]["quiz_artifact_id"] == artifact.artifact_id
    assert matrix["ku-1"][0]["question_count"] == 1
    assert matrix["ku-1"][0]["source_refs"] == ["src-1"]


def test_empty_quiz_artifact_is_a_warning_only_coverage_result() -> None:
    unit = _unit()
    artifact = _artifact(unit, questions=[])
    blueprint = LearningBlueprint(
        artifact_id="bp-empty-quiz",
        run_id="run-quiz",
        version=1,
        status="accepted",
        created_by="test",
        title="空题目测试",
        source_refs=["src-1"],
        knowledge_units=[unit],
    )

    report = validate_quiz_coverage([artifact], blueprint, valid_source_refs={"src-1"})

    assert report.status == "passed"
    assert report.missing_unit_ids == []
    assert report.objective_gaps[unit.artifact_id] == unit.learning_objectives
    assert report.matrix[0].question_count == 0
    assert report.matrix[0].valid_question_count == 0
    assert all(issue.severity == "warning" for issue in report.issues)


def test_failed_provider_response_is_retained_in_blocked_artifact() -> None:
    class FailingProvider:
        provider = "deepseek"
        model = "test"
        capabilities = type("Capabilities", (), {"max_output_tokens": 2200})()

        def generate_structured(self, request):
            raise ProviderError("invalid quiz JSON", category="schema", raw_output='{"questions": []}')

    unit = _unit()
    artifact = generate_quiz_artifact(FailingProvider(), task=_task(unit), unit=unit, context_pack=_pack(unit))
    assert artifact.status == "blocked"
    assert artifact.raw_response == '{"questions": []}'
    assert artifact.generation_error == "invalid quiz JSON"
    assert any(issue.severity == "blocking" for issue in artifact.issues)


def test_typed_draft_failure_keeps_provider_raw_response() -> None:
    class BrokenValue:
        def model_dump(self, **_kwargs):
            raise ValueError("draft conversion failed")

    class ProviderWithMalformedDraft:
        provider = "deepseek"
        model = "test"
        capabilities = type("Capabilities", (), {"max_output_tokens": 2200})()

        def generate_structured(self, request):
            return type(
                "Response",
                (),
                {
                    "value": BrokenValue(),
                    "raw_text": '{"knowledge_unit_id":"ku-1","questions": [broken]}',
                    "provider": self.provider,
                    "model": self.model,
                    "base_url": None,
                    "config_version": "test-v1",
                    "duration_ms": 1,
                    "usage": {},
                },
            )()

    artifact = generate_quiz_artifact(
        ProviderWithMalformedDraft(),
        task=_task(_unit()),
        unit=_unit(),
        context_pack=_pack(_unit()),
    )
    assert artifact.status == "blocked"
    assert "draft conversion failed" in (artifact.generation_error or "")
    assert artifact.raw_response.startswith('{"knowledge_unit_id"')


def test_provider_draft_unit_mismatch_blocks_artifact() -> None:
    unit = _unit()

    class MismatchedProvider:
        provider = "deepseek"
        model = "test"
        capabilities = type("Capabilities", (), {"max_output_tokens": 2200})()

        def generate_structured(self, request):
            questions = [
                {
                    "question_id": "q-1",
                    "knowledge_unit_id": unit.artifact_id,
                    "target_objectives": [unit.learning_objectives[0]],
                    "question": "问题一",
                    "options": ["A", "B"],
                    "answer": "A",
                    "explanation": "解析一",
                    "source_refs": ["src-1"],
                },
                {
                    "question_id": "q-2",
                    "knowledge_unit_id": unit.artifact_id,
                    "target_objectives": [unit.learning_objectives[0]],
                    "question": "问题二",
                    "options": ["A", "B"],
                    "answer": "A",
                    "explanation": "解析二",
                    "source_refs": ["src-1"],
                },
            ]
            return type(
                "Response",
                (),
                {
                    "value": {"knowledge_unit_id": "wrong-unit", "target_objectives": [], "questions": questions},
                    "raw_text": '{"knowledge_unit_id":"wrong-unit"}',
                    "provider": self.provider,
                    "model": self.model,
                    "base_url": None,
                    "config_version": "test-v1",
                    "duration_ms": 1,
                    "usage": {},
                },
            )()

    provider = MismatchedProvider()
    original = provider.generate_structured

    def generate_structured(request):
        response = original(request)
        response.value = QuizDraft.model_validate(response.value)
        return response

    provider.generate_structured = generate_structured
    artifact = generate_quiz_artifact(provider, task=_task(unit), unit=unit, context_pack=_pack(unit))
    assert artifact.status == "blocked"
    assert any(issue.issue_id.startswith("quiz-draft-unit-mismatch") for issue in artifact.issues)


def test_quiz_prompt_allows_agent_to_select_count() -> None:
    unit = _unit()
    prompt = build_quiz_prompt(_task(unit), unit, _pack(unit))
    assert prompt.startswith("# Agent: generate_quiz_artifact")
    assert "src-1" in prompt
    assert "number may be zero" in prompt
    assert '"question_count"' in prompt
    assert "exactly `2` questions" not in prompt
    assert "{{CONTEXT_PACK}}" not in prompt
