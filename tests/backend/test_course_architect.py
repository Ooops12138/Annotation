from __future__ import annotations

from typing import Any

from annotation.domain.artifacts import ContentArtifact, ContentTask, ContextExcerpt, ContextPack, KnowledgeUnit, LearningBlueprint
from annotation.providers.models import ProviderCapabilities, StructuredGenerationRequest, StructuredGenerationResponse
from annotation.workflow.course_architect import plan_content_tasks_with_architect
from annotation.workflow.interactive_components import plan_interactive_component
from annotation.workflow.quiz import build_quiz_prompt


class ArchitectProvider:
    provider = "architect-test"
    model = "architect-test-model"
    base_url = None
    config_version = "test-v1"
    capabilities = ProviderCapabilities(supports_structured_output=True)

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[StructuredGenerationRequest[Any]] = []

    def generate_structured(self, request: StructuredGenerationRequest[Any]) -> StructuredGenerationResponse[Any]:
        self.calls.append(request)
        value = request.schema.model_validate(self.payload)
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


def _unit(unit_id: str, title: str, refs: list[str]) -> KnowledgeUnit:
    return KnowledgeUnit(
        artifact_id=unit_id,
        run_id="run-architect",
        version=1,
        status="accepted",
        source_refs=refs,
        created_by="test",
        title=title,
        kind="concept",
        learning_objectives=[f"理解{title}"],
    )


def _blueprint() -> LearningBlueprint:
    units = [_unit("ku-sup", "上确界", ["src-1"]), _unit("ku-basic", "基础概念", ["src-2"])]
    return LearningBlueprint(
        artifact_id="bp-architect",
        run_id="run-architect",
        version=1,
        status="accepted",
        source_refs=["src-1", "src-2"],
        created_by="test",
        title="测试章节",
        knowledge_units=units,
    )


def test_course_architect_generates_structured_content_tasks() -> None:
    provider = ArchitectProvider({
        "tasks": [
            {
                "knowledge_unit_id": "ku-sup",
                "quiz_count": 2,
                "depth_guidance": "detailed",
                "source_refs": ["src-1", "unknown"],
                "interactive_component_policy": "required",
                "content_agent_strategy": "parallel",
                "execution_group": 1,
                "acceptance_criteria": ["说明上确界与最大元的差别。"],
            },
            {
                "knowledge_unit_id": "ku-basic",
                "quiz_count": 0,
                "depth_guidance": "concise",
                "source_refs": ["src-2"],
                "interactive_component_policy": "skip",
                "content_agent_strategy": "single",
                "execution_group": 2,
                "acceptance_criteria": [],
            },
        ]
    })

    result = plan_content_tasks_with_architect(provider, run_id="run-architect", blueprint=_blueprint())

    tasks = result["content_tasks"]
    assert [call.schema.__name__ for call in provider.calls] == ["ContentTaskPlanDraft"]
    assert [task.knowledge_unit_id for task in tasks] == ["ku-sup", "ku-basic"]
    assert tasks[0].quiz_count == 2
    assert tasks[0].interactive_component_policy == "required"
    assert tasks[0].content_agent_strategy == "parallel"
    assert tasks[0].source_refs == []
    assert not any("dropped_source_refs" in warning for warning in result["warnings"])
    assert "说明上确界与最大元的差别。" in tasks[0].acceptance_criteria


def test_large_blueprint_task_planning_batches_without_losing_global_order(monkeypatch) -> None:
    import json
    from annotation.workflow.budget import PromptBudget
    from annotation.workflow.models import ContentTaskPlanDraft

    class BatchProvider(ArchitectProvider):
        def generate_structured(self, request):
            context = json.loads(request.prompt.split("## Learning Blueprint", 1)[1].split("```json", 1)[1].split("```", 1)[0])
            self.payload = {"tasks": [{"knowledge_unit_id": u["knowledge_unit_id"], "quiz_count": 0,
                "depth_guidance": "detailed", "execution_group": index + 1} for index, u in enumerate(context["knowledge_units"])]}
            return super().generate_structured(request)

    blueprint = _blueprint()
    blueprint.knowledge_units = [_unit(f"unit-{i}", f"知识单元{i}", []) for i in range(23)]
    provider = BatchProvider({})
    result = plan_content_tasks_with_architect(provider, run_id="batch-run", blueprint=blueprint)
    assert result["content_task_planning_metadata"]["status"] == "succeeded"
    assert len(provider.calls) == 3
    assert [t.knowledge_unit_id for t in result["content_tasks"]] == [u.artifact_id for u in blueprint.knowledge_units]
    assert len({t.task_id for t in result["content_tasks"]}) == 23
    assert [t.execution_group for t in result["content_tasks"]] == list(range(1, 24))
    assert all(t.depth_guidance == "detailed" and t.quiz_count == 0 for t in result["content_tasks"])
    budget = PromptBudget.for_provider(provider, output_limit=5000)
    assert all(budget.fits(call.prompt, ContentTaskPlanDraft) for call in provider.calls)


def test_oversized_single_architect_unit_blocks_without_a_model_call(monkeypatch) -> None:
    blueprint = _blueprint()
    blueprint.knowledge_units[0].learning_objectives = ["很长的目标" * 10000]
    provider = ArchitectProvider({"tasks": []})
    result = plan_content_tasks_with_architect(provider, run_id="oversized-plan", blueprint=blueprint)
    assert provider.calls == []
    assert result["content_task_planning_metadata"]["status"] != "succeeded"


def test_duplicate_architect_tasks_require_revision() -> None:
    provider = ArchitectProvider({"tasks": [{"knowledge_unit_id": unit_id, "depth_guidance": "standard"} for unit_id in ("ku-sup", "ku-sup", "ku-basic")]})
    result = plan_content_tasks_with_architect(provider, run_id="duplicate-plan", blueprint=_blueprint())
    assert result["content_task_planning_metadata"]["status"] == "needs_revision"


def test_quiz_prompt_uses_architect_question_count() -> None:
    unit = _unit("ku-sup", "上确界", ["src-1"])
    task = ContentTask(
        depth_guidance="standard",
        task_id="task-1",
        run_id="run-architect",
        blueprint_version="bp-architect:v1",
        knowledge_unit_id=unit.artifact_id,
        quiz_count=2,
        source_refs=["src-1"],
    )
    pack = ContextPack(
        context_pack_id="ctx-task-1",
        run_id="run-architect",
        task_id=task.task_id,
        knowledge_unit_id=unit.artifact_id,
        source_refs=["src-1"],
        selected_source_refs=["src-1"],
        excerpts=[ContextExcerpt(source_ref="src-1", page_number=1, block_index=0, text="上确界是最小上界。")],
        estimated_input_tokens=10,
        input_budget_tokens=100,
    )

    prompt = build_quiz_prompt(task, unit, pack)

    assert '"quiz_count": 2' in prompt
    assert "Generate exactly 2 questions" in prompt


def test_interactive_planner_respects_content_task_policy() -> None:
    unit_required = _unit("ku-required", "基础概念", ["src-1"])
    unit_skip = _unit("ku-skip", "上确界", ["src-2"])
    required_task = ContentTask(
        depth_guidance="standard",
        task_id="task-required",
        run_id="run-architect",
        blueprint_version="bp-architect:v1",
        knowledge_unit_id=unit_required.artifact_id,
        source_refs=["src-1"],
        interactive_component_policy="required",
    )
    skip_task = ContentTask(
        depth_guidance="standard",
        task_id="task-skip",
        run_id="run-architect",
        blueprint_version="bp-architect:v1",
        knowledge_unit_id=unit_skip.artifact_id,
        source_refs=["src-2"],
        interactive_component_policy="skip",
    )
    packs = [
        ContextPack(context_pack_id="ctx-required", run_id="run-architect", task_id=required_task.task_id, knowledge_unit_id=unit_required.artifact_id, source_refs=["src-1"], selected_source_refs=["src-1"], excerpts=[], estimated_input_tokens=1, input_budget_tokens=10),
        ContextPack(context_pack_id="ctx-skip", run_id="run-architect", task_id=skip_task.task_id, knowledge_unit_id=unit_skip.artifact_id, source_refs=["src-2"], selected_source_refs=["src-2"], excerpts=[], estimated_input_tokens=1, input_budget_tokens=10),
    ]
    artifacts = [
        ContentArtifact(artifact_id="content-required", run_id="run-architect", version=1, status="accepted", source_refs=["src-1"], created_by="test", knowledge_unit_ids=[unit_required.artifact_id], task_id=required_task.task_id, context_pack_id="ctx-required", title="基础概念", content="静态内容"),
        ContentArtifact(artifact_id="content-skip", run_id="run-architect", version=1, status="accepted", source_refs=["src-2"], created_by="test", knowledge_unit_ids=[unit_skip.artifact_id], task_id=skip_task.task_id, context_pack_id="ctx-skip", title="上确界", content="上确界适合图示，但该任务要求跳过组件。"),
    ]

    plan, task, *_ = plan_interactive_component(
        run_id="run-architect",
        tasks=[required_task, skip_task],
        context_packs=packs,
        content_artifacts=artifacts,
        units=[unit_required, unit_skip],
    )

    assert plan.status == "planned"
    assert plan.knowledge_unit_id == unit_required.artifact_id
    assert task is not None
    assert task.knowledge_unit_id == unit_required.artifact_id
