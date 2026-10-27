"""A-005 course architect: structured ContentTask planning."""

from __future__ import annotations

import json
from typing import Any, Iterable

from annotation.domain.artifacts import ContentTask, LearningBlueprint, KnowledgeUnit
from annotation.prompt_loader import load_prompt
from annotation.providers import ModelProvider, ProviderError, StructuredGenerationRequest
from annotation.workflow.helpers import response_metadata, provider_metadata
from annotation.workflow.content_support import _plan_content_tasks
from annotation.workflow.models import ContentTaskPlanDraft
from annotation.workflow.budget import PromptBudget


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
                    "learning_objectives": unit.objective_context(),
                    "prerequisites": list(unit.prerequisites),
                    "related_unit_ids": list(unit.related_unit_ids),
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
        criteria.append("尝试生成基于教材来源且通过沙盒检查的交互式组件；失败时省略组件，不阻塞讲解和练习")
    elif draft is not None and draft.interactive_component_policy == "skip":
        criteria.append("该单元不生成交互式组件，使用静态讲解和测验完成学习闭环")
    return criteria


def _tasks_from_draft(
    *,
    run_id: str,
    blueprint: LearningBlueprint,
    draft: ContentTaskPlanDraft,
) -> tuple[list[ContentTask], list[str]]:
    units = {u.artifact_id: u for u in blueprint.knowledge_units}
    warnings: list[str] = []
    covered: dict[str, set[str]] = {}
    tasks: list[ContentTask] = []
    occurrences = {key: sum(key in item.covered_objectives for item in draft.tasks) for key in units}
    for index, item in enumerate(draft.tasks, 1):
        invalid = False
        for key, objectives in item.covered_objectives.items():
            if key not in units or not set(objectives).issubset(units[key].objective_ids):
                warnings.append(f"course_architect_revision_required:{key}:unknown_unit_or_objective")
                invalid = True
                continue
            if units[key].learning_objectives and not objectives:
                warnings.append(f"course_architect_revision_required:{key}:empty_scope")
                invalid = True
            covered.setdefault(key, set()).update(objectives)
        if invalid:
            continue
        keys = list(item.covered_objectives)
        if not keys:
            warnings.append(f"course_architect_revision_required:task-{index}:empty_scope")
            continue
        unit_id = keys[0] if len(keys) == 1 and occurrences[keys[0]] == 1 else f"teaching-{run_id}-{index:03d}"
        tasks.append(ContentTask(task_id=f"task-{run_id}-{index:03d}", run_id=run_id,
            blueprint_version=f"{blueprint.artifact_id}:v{blueprint.version}",
            knowledge_unit_id=unit_id, title=item.title, covered_objectives=item.covered_objectives,
            quiz_count=item.quiz_count, depth_guidance=item.depth_guidance,
            interactive_component_policy=item.interactive_component_policy,
            acceptance_criteria=_unique([
                *(criterion for key in keys for criterion in _default_criteria(units[key], item)),
                *item.acceptance_criteria,
            ])))
    for key, unit in units.items():
        if key not in covered or set(unit.objective_ids) - covered[key]:
            warnings.append(f"course_architect_revision_required:{key}:missing_objectives")
    return tasks, warnings


def teaching_blueprint(blueprint: LearningBlueprint, tasks: list[ContentTask]) -> LearningBlueprint:
    """Execution view; the original Blueprint is never rewritten."""
    originals = {u.artifact_id: u for u in blueprint.knowledge_units}
    units = []
    for task in tasks:
        selected = [originals[key] for key in task.covered_objectives]
        objective_map = {o.objective_id: o for u in selected for o in u.learning_objectives}
        objectives = [objective_map[oid] for oid in _unique(
            o for values in task.covered_objectives.values() for o in values)]
        prerequisites = _unique(p for u in selected for p in u.prerequisites if p not in task.covered_objectives)
        units.append(KnowledgeUnit(artifact_id=task.knowledge_unit_id, run_id=task.run_id,
            version=1, status="accepted", created_by="course_architect",
            title=task.title, kind=selected[0].kind, learning_objectives=objectives,
            prerequisites=prerequisites, source_refs=_unique(r for u in selected for r in u.source_refs)))
    return blueprint.model_copy(update={"knowledge_units": units})


def _plan_content_task_batch(
    provider: ModelProvider,
    *,
    run_id: str,
    blueprint: LearningBlueprint,
    batch_index: int = 0,
    global_offset: int = 0,
) -> dict[str, Any]:
    """Return ContentTask objects planned by the course architect agent."""

    provider_name = str(getattr(provider, "provider", "unknown"))
    prompt = load_prompt("plan_content_tasks", BLUEPRINT=_blueprint_context(blueprint))
    metadata: dict[str, Any] = {
        "agent": "plan_content_tasks",
        "prompt_version": "plan_content_tasks:v3:optional-subject",
        "run_id": run_id,
        "blueprint_artifact_id": blueprint.artifact_id,
        "blueprint_version": blueprint.version,
        "prompt": prompt,
        "batch_index": batch_index,
        "global_offset": global_offset,
    }
    warnings: list[str] = []
    raw_output = ""
    status = "succeeded"
    try:
        budget = PromptBudget.for_provider(provider, output_limit=5000)
        workflow_capabilities = getattr(provider, "workflow", None)
        if workflow_capabilities is not None:
            draft = workflow_capabilities.plan_content_tasks(blueprint)
            raw_output = draft.model_dump_json()
            metadata.update(workflow_capabilities.fixture_metadata("plan_content_tasks"))
        else:
            budget.check(prompt, ContentTaskPlanDraft)
            response = provider.generate_structured(StructuredGenerationRequest(
                prompt=prompt,
                schema=ContentTaskPlanDraft,
                max_output_tokens=budget.output_tokens,
                metadata={key: value for key, value in metadata.items() if key != "prompt"},
            ))
            draft = ContentTaskPlanDraft.model_validate(response.value.model_dump(mode="python"))
            raw_output = response.raw_text
            metadata.update(response_metadata(response))
        tasks, planning_warnings = _tasks_from_draft(run_id=run_id, blueprint=blueprint, draft=draft)
        warnings.extend(planning_warnings)
        if any(item.startswith("course_architect_revision_required:") for item in planning_warnings):
            status = "needs_revision"
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
        warnings.append(f"course_architect_blocked:{status}:{exc}")
        metadata.update({
            **provider_metadata(provider),
            "status": status,
            "raw_output": str(getattr(exc, "raw_output", "") or raw_output),
            "error": str(exc),
            "error_category": getattr(exc, "category", "provider"),
        })
    except Exception as exc:
        status = "runtime_error"
        warnings.append(f"course_architect_blocked:{status}:{exc}")
        metadata.update({
            **provider_metadata(provider),
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


def plan_content_tasks_with_architect(
    provider: ModelProvider, *, run_id: str, blueprint: LearningBlueprint,
) -> dict[str, Any]:
    """Plan bounded batches without losing global IDs, task order or diagnostics."""
    # Organization is global: batching by source units would prohibit cross-batch merges.
    return _plan_content_task_batch(provider, run_id=run_id, blueprint=blueprint)
