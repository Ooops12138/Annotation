"""Deterministic interactive-component fixtures for offline runs."""

from annotation.domain.artifacts import (
    ComponentAccessibility,
    ComponentTestAction,
    InteractiveComponentTask,
    IntervalLineSpec,
    ToggleControlSpec,
)
from annotation.workflow.models import InteractiveComponentDraft


def component_draft(task: InteractiveComponentTask) -> InteractiveComponentDraft:
    spec = IntervalLineSpec(
        component_id="interval-supremum-line",
        title="观察区间的右端点与上确界",
        learning_objective="区分区间 [0,1) 的上确界 1 与不存在的最大元。",
        source_refs=list(task.source_refs),
        accessibility=ComponentAccessibility(
            aria_label="区间 [0,1) 的数轴图示",
            description="数轴显示从 0 到 1 的区间，右端点为空心圆，因此 1 不属于该集合。",
            observation="右端点 1 是上确界，但由于端点开放，集合没有最大元。",
        ),
        controls=[ToggleControlSpec(
            control_id="show-supremum",
            label="标出上确界",
            default_value=False,
        )],
        test_actions=[ComponentTestAction(
            action="toggle",
            control_id="show-supremum",
            value=True,
            expected_text="上确界",
        )],
        annotations=[],
        interval_start=0,
        interval_end=1,
        left_endpoint="closed",
        right_endpoint="open",
        supremum=1,
        maximum=None,
    )
    return InteractiveComponentDraft(
        task_id=task.task_id,
        knowledge_unit_id=task.knowledge_unit_id,
        context_pack_id=task.context_pack_id,
        spec=spec,
    )
