from annotation.domain.artifacts import ContentTask, KnowledgeUnit, LearningBlueprint, SourceBlock
from annotation.workflow.content import build_context_pack, estimate_tokens, render_context_pack


def _block(ref: str, index: int, text: str) -> SourceBlock:
    return SourceBlock(
        artifact_id=f"{ref}-artifact",
        run_id="run-context",
        version=1,
        status="draft",
        source_refs=[ref],
        created_by="test",
        source_ref=ref,
        document_id="doc-context",
        page_number=1,
        block_index=index,
        text=text,
        raw_text=text,
        text_hash=f"hash-{index}",
        parser_version="test",
        bbox=(0, 0, 1, 1),
    )


def test_context_pack_prioritizes_declared_refs_and_neighbors() -> None:
    blocks = [
        _block("src-1", 1, "上确界是所有上界中最小的上界。"),
        _block("src-2", 2, "最大元必须属于集合。"),
        _block("src-3", 3, "完全公理说明非空有上界集合有最小上界。"),
    ]
    unit = KnowledgeUnit(
        artifact_id="ku-sup",
        run_id="run-context",
        version=1,
        status="accepted",
        created_by="test",
        title="上确界",
        kind="concept",
        learning_objectives=["理解上确界定义"],
        prerequisites=["实数序结构"],
        source_refs=["src-1"],
    )
    blueprint = LearningBlueprint(
        artifact_id="bp-context",
        run_id="run-context",
        version=1,
        status="accepted",
        created_by="test",
        title="测试章节",
        knowledge_units=[unit],
    )
    task = ContentTask(
        task_id="task-context-001",
        run_id="run-context",
        blueprint_version="bp-context:v1",
        knowledge_unit_id=unit.artifact_id,
        source_refs=["src-1"],
    )
    pack = build_context_pack(task, unit, blueprint, blocks, context_window=1000, reserved_output_tokens=700, safety_margin_tokens=100)
    assert pack.source_refs[0] == "src-1"
    assert "src-2" in pack.source_refs
    assert pack.estimated_input_tokens <= pack.input_budget_tokens
    assert pack.source_snapshot
    assert "[src-1" in render_context_pack(pack)


def test_context_pack_records_omitted_required_sources_when_budget_is_tiny() -> None:
    blocks = [_block("src-1", 1, "上确界。" * 900), _block("src-2", 2, "完全性。" * 900)]
    unit = KnowledgeUnit(
        artifact_id="ku-tiny",
        run_id="run-context",
        version=1,
        status="accepted",
        created_by="test",
        title="上确界",
        kind="concept",
        source_refs=["src-1", "src-2"],
    )
    blueprint = LearningBlueprint(
        artifact_id="bp-tiny",
        run_id="run-context",
        version=1,
        status="accepted",
        created_by="test",
        title="测试章节",
        knowledge_units=[unit],
    )
    task = ContentTask(
        task_id="task-tiny-001",
        run_id="run-context",
        blueprint_version="bp-tiny:v1",
        knowledge_unit_id=unit.artifact_id,
        source_refs=["src-1", "src-2"],
    )
    pack = build_context_pack(task, unit, blueprint, blocks, context_window=900, reserved_output_tokens=850, safety_margin_tokens=0)
    assert pack.input_budget_tokens == 50
    assert estimate_tokens("abcd") == 1
    assert pack.source_refs == []
    assert pack.omitted_source_refs == ["src-1", "src-2"]
