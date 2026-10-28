"""Model-local keys never become the identity of newly persisted units."""

import pytest

from annotation.providers import ProviderError
from annotation.workflow.helpers import blueprint_from_draft
from annotation.workflow.models import BlueprintDraft


def convert(units, previous=None):
    return blueprint_from_draft(
        draft=BlueprintDraft(title="Book", knowledge_units=units),
        state={"run_id": "r", "source_refs": [], "blueprint": previous},
        provider_name="test",
    )[0]


def test_new_ids_are_allocated_and_forward_and_back_references_are_rewritten():
    bp = convert([
        {"knowledge_unit_id": "u1", "title": "First", "related_unit_ids": ["u2"],
         "learning_objectives": ["Goal"]},
        {"knowledge_unit_id": "u2", "title": "Second", "prerequisites": ["u1"]},
    ])
    first, second = bp.knowledge_units
    assert first.artifact_id.startswith("ku-")
    assert second.artifact_id.startswith("ku-")
    assert first.artifact_id != second.artifact_id
    assert first.related_unit_ids == [second.artifact_id]
    assert second.prerequisites == [first.artifact_id]
    assert first.objective_ids == [f"{first.artifact_id}-o1"]


def test_same_titles_without_keys_receive_distinct_program_ids():
    first, second = convert([{"title": "Same"}, {"title": "Same"}]).knowledge_units
    assert first.artifact_id != second.artifact_id


def test_model_permanent_looking_key_is_still_only_a_temporary_key():
    first = convert([{"knowledge_unit_id": "ku-model-invented", "title": "Unit"}]).knowledge_units[0]
    assert first.artifact_id != "ku-model-invented"


@pytest.mark.parametrize("field", ["prerequisites", "related_unit_ids"])
def test_unknown_references_are_rejected(field):
    with pytest.raises(ProviderError, match="unknown knowledge unit reference"):
        convert([{"knowledge_unit_id": "u1", "title": "Unit", field: ["missing"]}])


def test_duplicate_local_keys_are_rejected():
    with pytest.raises(ProviderError, match="duplicate knowledge unit id"):
        convert([{"knowledge_unit_id": "u1", "title": "First"},
                 {"knowledge_unit_id": "u1", "title": "Second"}])


def test_revision_retains_existing_identity_and_allocates_added_unit():
    original = convert([{"knowledge_unit_id": "u1", "title": "Before",
                         "learning_objectives": ["Original objective"]}])
    retained = original.knowledge_units[0]
    revised = convert([
        {"knowledge_unit_id": "new", "title": "Added", "prerequisites": [retained.artifact_id]},
        {"knowledge_unit_id": retained.artifact_id, "title": "Renamed",
         "learning_objectives": [retained.learning_objectives[0]], "related_unit_ids": ["new"]},
    ], previous=original)
    added, existing = revised.knowledge_units
    assert existing.artifact_id == retained.artifact_id
    assert existing.objective_ids == retained.objective_ids
    assert added.artifact_id not in {"new", retained.artifact_id}
    assert added.prerequisites == [existing.artifact_id]
    assert existing.related_unit_ids == [added.artifact_id]


def test_references_to_removed_units_are_rejected():
    original = convert([{"knowledge_unit_id": "u1", "title": "Removed"}])
    with pytest.raises(ProviderError, match="unknown knowledge unit reference"):
        convert([{"knowledge_unit_id": "new", "title": "Added",
                  "prerequisites": [original.knowledge_units[0].artifact_id]}], previous=original)
