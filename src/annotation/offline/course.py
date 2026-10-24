"""Deterministic course-plan fixtures for offline runs."""

from annotation.domain.artifacts import LearningBlueprint
from annotation.workflow.models import ContentTaskDraft, ContentTaskPlanDraft


def task_plan(blueprint: LearningBlueprint) -> ContentTaskPlanDraft:
    tasks: list[ContentTaskDraft] = []
    for unit in blueprint.knowledge_units:
        objective_count = len(unit.learning_objectives)
        wants_component = any(
            token in " ".join([unit.title, *unit.learning_objectives])
            for token in ("上确界", "区间", "函数", "复数", "数轴")
        )
        tasks.append(ContentTaskDraft(
            title=unit.title,
            covered_objectives={unit.artifact_id: list(unit.learning_objectives)},
            quiz_count=objective_count or None,
            depth_guidance="standard",
            interactive_component_policy="required" if wants_component else "skip",
            acceptance_criteria=[],
        ))
    return ContentTaskPlanDraft(tasks=tasks)
