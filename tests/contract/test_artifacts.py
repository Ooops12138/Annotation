from annotation.domain.artifacts import LearningBlueprint


def test_minimal_blueprint_artifact_validates() -> None:
    artifact = LearningBlueprint(
        artifact_id="bp-001",
        run_id="run-001",
        version=1,
        status="draft",
        created_by="test",
        title="Fixture blueprint",
    )
    assert artifact.model_dump()["artifact_id"] == "bp-001"
