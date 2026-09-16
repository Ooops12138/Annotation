from __future__ import annotations

import re
from typing import Any

from annotation.domain.artifacts import (
    ContentArtifact,
    ContentTask,
    ContextExcerpt,
    ContextPack,
    FactCheckPolicy,
    KnowledgeUnit,
    LearningBlueprint,
    QuizArtifact,
    QuizQuestion,
    SourceBlock,
    SourceDocument,
)
from annotation.providers.models import (
    ProviderCapabilities,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)
from annotation.fact_checking import EvidenceKind, EvidenceRecord, SearchResult, SearchStatus
from annotation.workflow.fact_check import run_fact_check_loop
from annotation.workflow import run_minimal_workflow


class ScriptedFactCheckProvider:
    provider = "fact-check-scripted"
    model = "fact-check-scripted-model"
    base_url = None
    config_version = "test-v1"
    capabilities = ProviderCapabilities(supports_structured_output=True, context_window=12000)

    def __init__(
        self,
        *,
        claim_target_index: int,
        claim_text: str,
        verdict: str,
        claims_through_round: int = 0,
        corrected_content: str = "教材中，上确界是最小上界。",
        query: str = "上确界",
    ) -> None:
        self.claim_target_index = claim_target_index
        self.claim_text = claim_text
        self.verdict = verdict
        self.claims_through_round = claims_through_round
        self.corrected_content = corrected_content
        self.query = query
        self.calls: list[StructuredGenerationRequest[Any]] = []

    def generate_structured(self, request: StructuredGenerationRequest[Any]) -> StructuredGenerationResponse[Any]:
        self.calls.append(request)
        schema_name = request.schema.__name__
        if schema_name == "FactCheckClaimsDraft":
            target_section = request.prompt.split("## Checkable targets", maxsplit=1)[-1]
            target_ids = list(dict.fromkeys(re.findall(r'"target_id":\s*"([^"]+)"', target_section)))
            round_number = int(request.metadata["round"])
            payload = {
                "claims": [{
                    "target_id": target_ids[self.claim_target_index],
                    "kind": "fact",
                    "text": self.claim_text,
                    "query": self.query,
                }]
                if round_number <= self.claims_through_round else [],
            }
        elif schema_name == "FactCheckAssessmentsDraft":
            claim_ids = list(dict.fromkeys(re.findall(r'"claim_id":\s*"([^"]+)"', request.prompt)))
            payload = {
                "assessments": [{
                    "claim_id": claim_id,
                    "verdict": self.verdict,
                    "judgement": "教材片段明确给出上确界是最小上界。",
                    "sufficient_textbook_evidence": self.verdict == "contradicted",
                } for claim_id in claim_ids],
            }
        elif schema_name == "ContentDraft":
            payload = {
                "title": "上确界",
                "content": (
                    getattr(self, "initial_content", None)
                    if request.metadata.get("agent") == "generate_content_artifact"
                    else self.corrected_content
                ) or self.corrected_content,
                "callouts": [],
                "source_refs": ["src-1"],
            }
        elif schema_name == "ContentCritiqueDraft":
            payload = {"issues": []}
        elif schema_name == "QuizDraft":
            payload = {
                "knowledge_unit_id": "unit-1",
                "question_count": 1,
                "target_objectives": ["理解上确界"],
                "questions": [{
                    "question_id": "quiz-1-corrected",
                    "knowledge_unit_id": "unit-1",
                    "target_objectives": ["理解上确界"],
                    "question": "上确界是什么？",
                    "options": ["最小上界", "最大上界"],
                    "answer": "最小上界",
                    "explanation": "教材片段将上确界定义为最小上界。",
                    "source_refs": ["src-1"],
                }],
            }
        elif schema_name == "InteractiveComponentDraft":
            payload = {
                "task_id": request.metadata["task_id"],
                "knowledge_unit_id": request.metadata["knowledge_unit_id"],
                "context_pack_id": request.metadata["context_pack_id"],
                "spec": None,
                "not_needed_reason": "事实核查测试不生成交互组件。",
            }
        elif schema_name == "InteractiveComponentCritiqueDraft":
            payload = {"issues": []}
        else:  # pragma: no cover - keeps a new model call visible to this test.
            raise AssertionError(f"unexpected schema: {schema_name}")
        value = request.schema.model_validate(payload)
        return StructuredGenerationResponse(
            value=value,
            raw_text=value.model_dump_json(),
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            config_version=self.config_version,
            duration_ms=0,
            usage={},
        )


def _fixture(*, with_quiz: bool = False) -> tuple[
    LearningBlueprint,
    ContentTask,
    ContextPack,
    ContentArtifact,
    list[QuizArtifact],
    list[SourceBlock],
]:
    run_id = "fact-check-test"
    unit = KnowledgeUnit(
        artifact_id="unit-1",
        run_id=run_id,
        version=1,
        status="accepted",
        source_refs=["src-1"],
        created_by="test",
        title="上确界",
        kind="concept",
        learning_objectives=["理解上确界"],
    )
    blueprint = LearningBlueprint(
        artifact_id="blueprint-1",
        run_id=run_id,
        version=1,
        status="accepted",
        source_refs=["src-1"],
        created_by="test",
        title="测试章节",
        knowledge_units=[unit],
    )
    task = ContentTask(
        task_id="task-1",
        run_id=run_id,
        blueprint_version="blueprint-1:v1",
        knowledge_unit_id=unit.artifact_id,
        source_refs=["src-1"],
    )
    pack = ContextPack(
        context_pack_id="ctx-1",
        run_id=run_id,
        task_id=task.task_id,
        knowledge_unit_id=unit.artifact_id,
        source_refs=["src-1"],
        selected_source_refs=["src-1"],
        excerpts=[ContextExcerpt(source_ref="src-1", page_number=1, block_index=0, text="上确界是所有上界中最小的上界。")],
        estimated_input_tokens=20,
        input_budget_tokens=100,
    )
    content = ContentArtifact(
        artifact_id="content-1",
        run_id=run_id,
        version=1,
        status="accepted",
        source_refs=["src-1"],
        created_by="test",
        knowledge_unit_ids=[unit.artifact_id],
        title=unit.title,
        task_id=task.task_id,
        context_pack_id=pack.context_pack_id,
        prompt_version="test",
        content="上确界是最大的上界。",
    )
    blocks = [SourceBlock(
        artifact_id="source-1",
        source_ref="src-1",
        document_id="source-doc-1",
        run_id=run_id,
        version=1,
        status="draft",
        source_refs=["src-1"],
        created_by="test",
        page_number=1,
        block_index=0,
        text="上确界是所有上界中最小的上界。",
        text_hash="source-hash",
        parser_version="test",
        bbox=(0, 0, 1, 1),
    )]
    quizzes: list[QuizArtifact] = []
    if with_quiz:
        question = QuizQuestion(
            question_id="quiz-1",
            knowledge_unit_id=unit.artifact_id,
            target_objectives=list(unit.learning_objectives),
            question="上确界是什么？",
            options=["最小上界", "最大上界"],
            answer="最大上界",
            explanation="上确界是最大的上界。",
            source_refs=["src-1"],
        )
        quizzes.append(QuizArtifact(
            artifact_id="quiz-1",
            run_id=run_id,
            version=1,
            status="accepted",
            source_refs=["src-1"],
            created_by="test",
            knowledge_unit_ids=[unit.artifact_id],
            target_objectives=list(unit.learning_objectives),
            questions=[question],
            question_count=1,
            task_id=task.task_id,
            context_pack_id=pack.context_pack_id,
            prompt_version="test",
        ))
    return blueprint, task, pack, content, quizzes, blocks


def _run(provider: ScriptedFactCheckProvider, *, with_quiz: bool = False, corrections: int = 2) -> dict[str, Any]:
    blueprint, task, pack, content, quizzes, blocks = _fixture(with_quiz=with_quiz)
    return run_fact_check_loop(
        provider,
        run_id="fact-check-test",
        blueprint=blueprint,
        tasks=[task],
        context_packs=[pack],
        content_artifacts=[content],
        quiz_artifacts=quizzes,
        source_blocks=blocks,
        valid_source_refs=["src-1"],
        policy=FactCheckPolicy(max_corrections_per_unit=corrections),
    )


def test_direct_textbook_contradiction_revises_content_then_rechecks() -> None:
    provider = ScriptedFactCheckProvider(
        claim_target_index=0,
        claim_text="上确界是最大的上界。",
        verdict="contradicted",
        claims_through_round=0,
    )

    result = _run(provider)

    content = result["content_artifacts"][0]
    trace = result["fact_check_artifact"].unit_traces[0]
    assert content.version == 2
    assert content.status == "accepted"
    assert "最小上界" in content.content
    assert trace.corrections_used == 1
    assert len(trace.rounds) == 2
    assert trace.rounds[0].route == "revise_content"
    assert result["fact_check_status"] == "accepted"
    assert result["fact_check_issues"] == []


def test_direct_textbook_contradiction_regenerates_only_the_quiz() -> None:
    provider = ScriptedFactCheckProvider(
        claim_target_index=1,
        claim_text="最大上界",
        verdict="contradicted",
        claims_through_round=0,
    )

    result = _run(provider, with_quiz=True)

    quiz = result["quiz_artifacts"][0]
    trace = result["fact_check_artifact"].unit_traces[0]
    assert result["content_artifacts"][0].artifact_id == "content-1"
    assert quiz.version == 2
    assert quiz.artifact_id == "quiz-task-1-fact-1"
    assert quiz.questions[0].answer == "最小上界"
    assert trace.rounds[0].route == "regenerate_quiz"
    assert trace.final_quiz_artifact_id == quiz.artifact_id


def test_insufficient_textbook_evidence_is_neutral_and_never_corrected() -> None:
    provider = ScriptedFactCheckProvider(
        claim_target_index=0,
        claim_text="教材没有说明的断言。",
        verdict="insufficient",
    )

    result = _run(provider)

    trace = result["fact_check_artifact"].unit_traces[0]
    assert trace.corrections_used == 0
    assert trace.final_status == "at_risk"
    assert result["fact_check_status"] == "at_risk"
    assert all(call.schema.__name__ != "ContentDraft" for call in provider.calls)
    assert any(issue.category == "uncertainty" and issue.severity == "warning" for issue in result["fact_check_issues"])


def test_exhausted_direct_contradiction_keeps_at_risk_content_preview() -> None:
    provider = ScriptedFactCheckProvider(
        claim_target_index=0,
        claim_text="上确界是最大的上界。",
        verdict="contradicted",
        claims_through_round=2,
        corrected_content="上确界是最大的上界。",
    )

    result = _run(provider, corrections=2)

    trace = result["fact_check_artifact"].unit_traces[0]
    assert trace.corrections_used == 2
    assert trace.final_status == "at_risk"
    assert result["fact_check_status"] == "at_risk"
    assert result["content_artifacts"][0].status == "accepted"
    assert any(issue.category == "fact" and issue.source_refs == ["src-1"] for issue in result["fact_check_issues"])


class StaticWebSkill:
    name = "test-web"
    version = "v1"

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, *, limit: int = 3) -> SearchResult:
        self.calls += 1
        return SearchResult(
            skill_name=self.name,
            skill_version=self.version,
            query=query,
            status=SearchStatus.OK,
            evidence=(EvidenceRecord(
                evidence_id="web-evidence-1",
                kind=EvidenceKind.WEB,
                query=query,
                text="外部参考资料有不同表述。",
                locator="https://example.edu/reference",
                provider=self.name,
                excerpt="外部参考资料有不同表述。",
                text_hash="web-hash",
                url="https://example.edu/reference",
            ),),
        )


def test_external_conflict_is_neutral_and_never_triggers_correction() -> None:
    provider = ScriptedFactCheckProvider(
        claim_target_index=0,
        claim_text="外部资料可能不同意的断言。",
        verdict="external_conflict",
        query="未出现的检索词",
    )
    blueprint, task, pack, content, quizzes, blocks = _fixture()
    web = StaticWebSkill()

    result = run_fact_check_loop(
        provider,
        run_id="fact-check-test",
        blueprint=blueprint,
        tasks=[task],
        context_packs=[pack],
        content_artifacts=[content],
        quiz_artifacts=quizzes,
        source_blocks=blocks,
        valid_source_refs=["src-1"],
        policy=FactCheckPolicy(web_enabled=True),
        web_search_skill=web,
    )

    assert web.calls == 1
    assert result["fact_check_status"] == "at_risk"
    assert result["fact_check_artifact"].unit_traces[0].corrections_used == 0
    assert all(call.schema.__name__ != "ContentDraft" for call in provider.calls)
    assert any(issue.category == "conflict" for issue in result["fact_check_issues"])


def test_default_policy_does_not_invoke_an_injected_web_skill() -> None:
    provider = ScriptedFactCheckProvider(
        claim_target_index=0,
        claim_text="教材没有说明的断言。",
        verdict="insufficient",
        query="未出现的检索词",
    )
    blueprint, task, pack, content, quizzes, blocks = _fixture()
    web = StaticWebSkill()

    result = run_fact_check_loop(
        provider,
        run_id="fact-check-test",
        blueprint=blueprint,
        tasks=[task],
        context_packs=[pack],
        content_artifacts=[content],
        quiz_artifacts=quizzes,
        source_blocks=blocks,
        valid_source_refs=["src-1"],
        policy=FactCheckPolicy(),
        web_search_skill=web,
    )

    assert not FactCheckPolicy().web_enabled
    assert web.calls == 0
    assert result["fact_check_status"] == "at_risk"


def test_top_level_workflow_runs_fact_check_before_document_assembly(monkeypatch) -> None:
    blueprint, _task, _pack, _content, _quizzes, blocks = _fixture()
    run_id = "fact-check-top-level"
    base_unit = blueprint.knowledge_units[0]
    blueprint = blueprint.model_copy(update={
        "run_id": run_id,
        "knowledge_units": [
            base_unit.model_copy(update={"run_id": run_id}),
            base_unit.model_copy(update={
                "artifact_id": "unit-2",
                "run_id": run_id,
                "title": "补充定理",
                "kind": "theorem",
                "learning_objectives": ["理解补充定理"],
            }),
            base_unit.model_copy(update={
                "artifact_id": "unit-3",
                "run_id": run_id,
                "title": "补充例题",
                "kind": "example",
                "learning_objectives": ["理解补充例题"],
            }),
        ],
    })

    def parse_fixture(_path, *, run_id: str):
        source = SourceDocument(
            artifact_id=f"source-document-{run_id}",
            run_id=run_id,
            version=1,
            status="accepted",
            source_refs=["src-1"],
            created_by="test",
            title="测试教材",
            locator="fixture://textbook",
        )
        return source, [block.model_copy(update={"run_id": run_id, "document_id": source.artifact_id}) for block in blocks]

    import annotation.workflow.graph as graph_module

    monkeypatch.setattr(graph_module, "parse_pdf", parse_fixture)
    monkeypatch.setattr(graph_module, "extraction_warnings", lambda _blocks: [])
    provider = ScriptedFactCheckProvider(
        claim_target_index=0,
        claim_text="上确界是最大的上界。",
        verdict="contradicted",
        claims_through_round=0,
    )
    provider.initial_content = "上确界是最大的上界。"

    state = run_minimal_workflow(
        provider=provider,
        run_id=run_id,
        blueprint=blueprint,
    )

    assert state["fact_check_status"] == "accepted"
    assert state["fact_check_artifact"].unit_traces[0].corrections_used == 1
    assert "最小上界" in state["content_artifacts"][0].content
    rendered_markdown = [node.content for section in state["document"].sections for node in section.children if node.type == "markdown"]
    assert any("最小上界" in content for content in rendered_markdown)
