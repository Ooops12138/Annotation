"""Objective identity survives wording, organization and persistence changes."""

import pytest
from pydantic import ValidationError

from annotation.domain.artifacts import KnowledgeUnit, LearningBlueprint
from annotation.providers import ProviderError
from annotation.workflow.blueprint_checks import validate_blueprint
from annotation.workflow.course_architect import _tasks_from_draft, teaching_blueprint
from annotation.workflow.helpers import blueprint_from_draft
from annotation.workflow.models import BlueprintDraft, ContentTaskPlanDraft


def unit(unit_id="ku-a", objectives=None):
    return KnowledgeUnit(artifact_id=unit_id, run_id="r", version=1, status="accepted",
        created_by="test", title=unit_id, kind="concept",
        learning_objectives=objectives if objectives is not None else ["Original wording", "Another goal"])


def blueprint(*units):
    return LearningBlueprint(artifact_id="bp", run_id="r", version=1, status="accepted",
        created_by="test", title="Book", knowledge_units=list(units))


def revise(previous, objectives):
    draft = BlueprintDraft.model_validate({"title": "Book", "knowledge_units": [{
        "knowledge_unit_id": "ku-a", "title": "Unit", "learning_objectives": objectives}]})
    return blueprint_from_draft(draft=draft, state={"run_id": "r", "source_refs": [],
        "blueprint": previous}, provider_name="test")[0]


def plan(bp, scopes):
    draft = ContentTaskPlanDraft.model_validate({"tasks": [{"title": str(i),
        "covered_objectives": scope, "depth_guidance": "standard"} for i, scope in enumerate(scopes)]})
    return _tasks_from_draft(run_id="r", blueprint=bp, draft=draft)


def test_wording_and_order_changes_preserve_identity_after_serialization():
    original = blueprint(unit())
    revised = revise(original, [original.knowledge_units[0].learning_objectives[1],
        {"objective_id": "ku-a-o1", "text": "Reworded, with different punctuation!"}])
    restored = LearningBlueprint.model_validate_json(revised.model_dump_json())
    assert restored.knowledge_units[0].objective_ids == ["ku-a-o2", "ku-a-o1"]
    assert restored.knowledge_units[0].objective_texts[-1] == "Reworded, with different punctuation!"
    tasks, warnings = plan(restored, [{"ku-a": ["ku-a-o1", "ku-a-o2"]}])
    assert not warnings
    assert teaching_blueprint(restored, tasks).knowledge_units[0].objective_ids == ["ku-a-o1", "ku-a-o2"]


def test_deleted_ids_are_not_reused_across_multiple_revisions():
    original = blueprint(unit())
    first = revise(original, [original.knowledge_units[0].learning_objectives[0], "New goal"])
    assert first.knowledge_units[0].objective_ids == ["ku-a-o1", "ku-a-o3"]
    second = revise(LearningBlueprint.model_validate_json(first.model_dump_json()), ["Different goal"])
    assert second.knowledge_units[0].objective_ids == ["ku-a-o4"]
    assert second.objective_id_history == ["ku-a-o1", "ku-a-o2", "ku-a-o3", "ku-a-o4"]


def test_revision_cannot_invent_or_borrow_an_objective_id():
    with pytest.raises(ProviderError, match="unknown retained objective_id"):
        revise(blueprint(unit(), unit("ku-b")), [{"objective_id": "ku-b-o1", "text": "Borrowed"}])


def test_duplicate_ids_and_malformed_objectives_are_rejected():
    objective = {"objective_id": "ku-a-o1", "text": "Goal"}
    with pytest.raises(ValidationError, match="duplicate objective_id"):
        unit(objectives=[objective, objective])
    with pytest.raises(ValidationError):
        unit(objectives=[{"text": "Missing identity"}])
    bp = blueprint(unit(objectives=[objective]), unit("ku-b", [objective]))
    check = validate_blueprint(bp, valid_source_refs=set())
    assert check.status == "needs_revision"
    assert any(i.issue_id == "blueprint-duplicate-objective-id" for i in check.issues)


def test_split_and_combined_tasks_preserve_ids_even_for_identical_text():
    bp = blueprint(unit(objectives=["Same text", "Same text"]), unit("ku-b", ["Same text"]))
    scopes = [{"ku-a": ["ku-a-o1"]}, {"ku-a": ["ku-a-o2"], "ku-b": ["ku-b-o1"]}]
    tasks, warnings = plan(bp, scopes)
    assert not warnings
    view = teaching_blueprint(bp, tasks)
    assert view.knowledge_units[0].objective_ids == ["ku-a-o1"]
    assert view.knowledge_units[1].objective_ids == ["ku-a-o2", "ku-b-o1"]
    assert view.knowledge_units[1].objective_texts == ["Same text", "Same text"]
    assert bp.knowledge_units[0].objective_ids == ["ku-a-o1", "ku-a-o2"]


@pytest.mark.parametrize("invalid", ["Original wording", "ku-a-o99", "ku-b-o1"])
def test_task_planning_rejects_text_unknown_and_foreign_ids(invalid):
    tasks, warnings = plan(blueprint(unit()), [{"ku-a": [invalid]}])
    assert not tasks
    assert any("unknown_unit_or_objective" in w for w in warnings)
