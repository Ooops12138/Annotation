"""Deterministic interactive-component fixtures for offline runs."""

from annotation.domain.artifacts import (
    ComponentAccessibility,
    ComponentTestAction,
    InteractiveComponentTask,
    GeneratedHtmlSpec,
    ToggleControlSpec,
)
from annotation.workflow.models import InteractiveComponentDraft


def component_draft(task: InteractiveComponentTask) -> InteractiveComponentDraft:
    spec = GeneratedHtmlSpec(
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
        html='<h2>区间 [0,1)</h2><svg viewBox="0 0 300 70" role="img" aria-label="0 实心，1 空心"><path d="M30 35H270" stroke="teal" stroke-width="3"/><circle cx="30" cy="35" r="5"/><circle cx="270" cy="35" r="5" fill="white" stroke="teal"/></svg><label><input type="checkbox" data-component-control="show-supremum">标出上确界</label><p id="observation" aria-live="polite">右端点不属于集合，没有最大元。</p>',
        css='body { font-family: sans-serif; padding: 1rem; } svg { width: 100%; }',
        javascript="document.querySelector('input').addEventListener('change', event => { document.querySelector('#observation').textContent = event.target.checked ? '上确界为 1，但没有最大元。' : '右端点不属于集合，没有最大元。'; });",

    )
    return InteractiveComponentDraft(
        task_id=task.task_id,
        knowledge_unit_id=task.knowledge_unit_id,
        context_pack_id=task.context_pack_id,
        spec=spec,
    )
