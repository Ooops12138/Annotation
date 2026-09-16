"""A-005 course architect: structured ContentTask planning."""

from __future__ import annotations

import json
from typing import Any, Iterable

from annotation.domain.artifacts import ContentTask, LearningBlueprint
from annotation.prompt_loader import load_prompt
from annotation.providers import ModelProvider, ProviderError, StructuredGenerationRequest
from annotation.workflow.content_support import _plan_content_tasks
from annotation.workflow.graph import _metadata, _provider_metadata
from annotation.workflow.models import ContentTaskDraft, ContentTaskPlanDraft


def _blueprint_context(blueprint: LearningBlueprint) -> str:
    return json.dumps(
        {
            "artifact_id": blueprint.artifact_id,
            "version": blueprint.version,
            "title": blueprint.title,
            "knowledge_units": [
                {
                    "knowledge_unit_id": unit.artifact_id,
                    "title": unit.title,
                    "kind": unit.kind,
                    "learning_objectives": list(unit.learning_objectives),
                    "prerequisites": list(unit.prerequisites),
                    "related_unit_ids": list(unit.related_unit_ids),
                    "source_refs": list(unit.source_refs),
                }
                for unit in blueprint.knowledge_units
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _default_criteria(unit, draft: ContentTaskDraft | None = None) -> list[str]:
    criteria = [
        "覆盖该知识单元的学习目标",
        "只使用 ContextPack 中可定位的教材证据",
        "说明与直接前置知识的必要衔接" if unit.prerequisites else "使用适合初学者的分步解释",
    ]
    if draft is not None and draft.quiz_count is not None:
        criteria.append(f"生成 {draft.quiz_count} 道由课程架构师指定的单选题；题量可为 0，不额外补题")
    else:
        criteria.append("由 Agent 根据学习目标和教材证据决定是否生成练习及题量；不为凑数编题")
    if draft is not None and draft.interactive_component_policy == "required":
        criteria.append("该单元需要交互式组件；组件必须基于教材来源且通过沙盒检查")
    elif draft is not None and draft.interactive_component_policy == "skip":
        criteria.append("该单元不生成交互式组件，使用静态讲解和测验完成学习闭环")
    return criteria


def _tasks_from_draft(
    *,
    run_id: str,
    blueprint: LearningBlueprint,
    draft: ContentTaskPlanDraft,
) -> tuple[list[ContentTask], list[str]]:
    units = list(blueprint.knowledge_units)
    drafts_by_unit: dict[str, ContentTaskDraft] = {}
    warnings: list[str] = []
    for item in draft.tasks:
        if item.knowledge_unit_id in drafts_by_unit:
            warnings.append(f"course_architect_duplicate_task:{item.knowledge_unit_id}")
            continue
        drafts_by_unit[item.knowledge_unit_id] = item

    blueprint_version = f"{blueprint.artifact_id}:v{blueprint.version}"
    tasks: list[ContentTask] = []
    for index, unit in enumerate(units, start=1):
        item = drafts_by_unit.get(unit.artifact_id)
        if item is None:
            warnings.append(f"course_architect_missing_task:{unit.artifact_id}")
        allowed_refs = set(unit.source_refs)
        source_refs = _unique(ref for ref in (item.source_refs if item is not None else unit.source_refs) if ref in allowed_refs)
        dropped_refs = [ref for ref in (item.source_refs if item is not None else []) if ref not in allowed_refs]
        if dropped_refs:
            warnings.append(f"course_architect_dropped_source_refs:{unit.artifact_id}:{','.join(dropped_refs)}")
        criteria = _unique([
            *(_default_criteria(unit, item)),
            *((item.acceptance_criteria if item is not None else []) or []),
        ])
        tasks.append(ContentTask(
            task_id=f"task-{run_id}-{index:03d}",
            run_id=run_id,
            blueprint_version=blueprint_version,
            knowledge_unit_id=unit.artifact_id,
            content_types=["explanation", "quiz"],
            quiz_count=item.quiz_count if item is not None else None,
            source_refs=source_refs,
            interactive_component_policy=item.interactive_component_policy if item is not None else "auto",
            content_agent_strategy=item.content_agent_strategy if item is not None else "single",
            execution_group=item.execution_group if item is not None else index,
            acceptance_criteria=criteria,
        ))
    unknown_units = [item.knowledge_unit_id for item in draft.tasks if item.knowledge_unit_id not in {unit.artifact_id for unit in units}]
    for unit_id in unknown_units:
        warnings.append(f"course_architect_unknown_unit:{unit_id}")
    return tasks, warnings


def _mock_task_plan(blueprint: LearningBlueprint) -> ContentTaskPlanDraft:
    tasks: list[ContentTaskDraft] = []
    for index, unit in enumerate(blueprint.knowledge_units, start=1):
        objective_count = len(unit.learning_objectives)
        wants_component = any(
            token in " ".join([unit.title, *unit.learning_objectives])
            for token in ("上确界", "区间", "函数", "复数", "数轴")
        )
        tasks.append(ContentTaskDraft(
            knowledge_unit_id=unit.artifact_id,
            quiz_count=objective_count if unit.source_refs else None,
            source_refs=list(unit.source_refs),
            interactive_component_policy="auto" if wants_component else "skip",
            content_agent_strategy="single",
            execution_group=index,
            acceptance_criteria=[],
        ))
    return ContentTaskPlanDraft(tasks=tasks)


def plan_content_tasks_with_architect(
    provider: ModelProvider,
    *,
    run_id: str,
    blueprint: LearningBlueprint,
) -> dict[str, Any]:
    """Return ContentTask objects planned by the course architect agent."""

    provider_name = str(getattr(provider, "provider", "unknown"))
    prompt = load_prompt("plan_content_tasks", BLUEPRINT=_blueprint_context(blueprint))
    metadata: dict[str, Any] = {
        "agent": "plan_content_tasks",
        "run_id": run_id,
        "blueprint_artifact_id": blueprint.artifact_id,
        "blueprint_version": blueprint.version,
        "prompt": prompt,
    }
    warnings: list[str] = []
    raw_output = ""
    status = "succeeded"
    try:
        if provider_name == "mock":
            draft = _mock_task_plan(blueprint)
            raw_output = draft.model_dump_json()
            metadata.update({
                "provider": "mock",
                "model": getattr(provider, "model", "fixture-model"),
                "base_url": getattr(provider, "base_url", None),
                "config_version": getattr(provider, "config_version", "mock-v1"),
                "duration_ms": 0,
                "usage": {},
                "fixture_adaptation": True,
            })
        else:
            response = provider.generate_structured(StructuredGenerationRequest(
                prompt=prompt,
                schema=ContentTaskPlanDraft,
                max_output_tokens=2400,
                metadata={key: value for key, value in metadata.items() if key != "prompt"},
            ))
            draft = ContentTaskPlanDraft.model_validate(response.value.model_dump(mode="python"))
            raw_output = response.raw_text
            metadata.update(_metadata(response))
        tasks, planning_warnings = _tasks_from_draft(run_id=run_id, blueprint=blueprint, draft=draft)
        warnings.extend(planning_warnings)
        metadata.update({
            "status": status,
            "raw_output": raw_output,
            "parsed_output": draft.model_dump(mode="json"),
        })
        return {
            "content_tasks": tasks,
            "content_task_planning_metadata": metadata,
            "warnings": warnings,
        }
    except ProviderError as exc:
        status = "schema_error" if getattr(exc, "category", "provider") == "schema" else "provider_error"
        warnings.append(f"course_architect_fallback:{status}:{exc}")
        metadata.update({
            **_provider_metadata(provider),
            "status": status,
            "raw_output": str(getattr(exc, "raw_output", "") or raw_output),
            "error": str(exc),
            "error_category": getattr(exc, "category", "provider"),
        })
    except Exception as exc:
        status = "runtime_error"
        warnings.append(f"course_architect_fallback:{status}:{exc}")
        metadata.update({
            **_provider_metadata(provider),
            "status": status,
            "raw_output": raw_output,
            "error": str(exc),
            "error_category": "runtime",
        })

    return {
        "content_tasks": _plan_content_tasks(run_id, blueprint),
        "content_task_planning_metadata": metadata,
        "warnings": warnings,
    }
