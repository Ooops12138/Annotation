"""Deterministic quiz fixtures for offline runs."""

from annotation.domain.artifacts import ContextPack, KnowledgeUnit, QuizDraft, QuizQuestion


def quiz_draft(
    unit: KnowledgeUnit,
    context_pack: ContextPack,
    requested_count: int | None = None,
) -> QuizDraft:
    refs = list(dict.fromkeys([
        *context_pack.selected_source_refs,
        *context_pack.source_refs,
        *(excerpt.source_ref for excerpt in context_pack.excerpts),
    ]))
    objectives = list(unit.objective_ids)
    count = max(0, requested_count) if requested_count is not None else (len(objectives) if refs else 0)
    target_one = objectives[0] if objectives else ""
    target_two = objectives[1] if len(objectives) > 1 else target_one
    questions: list[QuizQuestion] = []
    for index in range(count):
        target = target_one if index % 2 == 0 else target_two
        options = [
            "当前 ContextPack 中的教材片段直接支持该说法",
            "该说法没有得到当前教材片段支持",
            "只能依据未提供的外部资料判断",
        ]
        questions.append(QuizQuestion(
            question_id=f"{unit.artifact_id}-quiz-{index + 1:02d}",
            knowledge_unit_id=unit.artifact_id,
            target_objectives=[target],
            question=f"关于“{unit.title}”，下列哪项可由当前教材依据支持？",
            options=options,
            answer=options[0],
            explanation="当前 ContextPack 的教材片段直接支持该表述。",
            source_refs=refs,
        ))
    return QuizDraft(
        knowledge_unit_id=unit.artifact_id,
        target_objectives=objectives,
        questions=questions,
    )
