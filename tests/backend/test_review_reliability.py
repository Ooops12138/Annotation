import json
from types import SimpleNamespace

import pytest

from annotation.domain.artifacts import FactCheckClaim, FactCheckEvidence, FactCheckPolicy, ContentAttemptTrace
from annotation.providers import ProviderError
from annotation.workflow.fact_check import _assess_claims, _issue_for_assessment, run_fact_check_loop
from annotation.workflow.content_reflection import build_content_reflection_subgraph
from annotation.prompt_archive import archive_prompts, write_artifact_json, read_artifact_json, restore_prompts
from annotation.token_estimation import estimate_tokens
from annotation.workflow.budget import encoded_size
from test_fact_check_loop import _fixture, ScriptedFactCheckProvider
from test_content_reflection_subgraph import _state, _draft, ScriptedContentProvider


def _run(provider, fixture, *, additional=False):
    blueprint, task, pack, content, quizzes, blocks = fixture
    return run_fact_check_loop(provider, run_id=content.run_id, blueprint=blueprint,
        tasks=[task], context_packs=[pack], content_artifacts=[content], quiz_artifacts=quizzes,
        source_blocks=blocks, valid_source_refs=["src-1"], policy=FactCheckPolicy(web_enabled=False),
        additional_retrieval_enabled=additional)


@pytest.mark.parametrize("recover", [False, True])
def test_quiz_correction_retries_temporary_errors_and_only_removes_conflicting_question(recover):
    fixture = _fixture(with_quiz=True)
    _, task, _, _, quizzes, _ = fixture
    task.quiz_count = 2 if not recover else 1
    if not recover:
        quizzes[0].questions.append(quizzes[0].questions[0].model_copy(update={
            "question_id": "unaffected", "answer": "最小上界", "explanation": "正确的解析",
        }))
    original = quizzes[0].model_dump()

    class Provider(ScriptedFactCheckProvider):
        correction_calls = 0
        def generate_structured(self, request):
            if request.metadata.get("agent") == "regenerate_quiz_for_fact_check":
                self.correction_calls += 1
                if not recover or self.correction_calls == 1:
                    raise ProviderError("temporary rate limit", category="rate_limit", retryable=True)
            return super().generate_structured(request)

    provider = Provider(claim_target_index=2, claim_text="上确界是最大的上界。",
                        verdict="contradicted", claims_through_round=0)
    result = _run(provider, fixture)
    assert provider.correction_calls == 2
    quiz = result["quiz_artifacts"][0]
    assert quiz.questions[0].answer == "最小上界"
    assert quizzes[0].model_dump() == original
    trace = result["fact_check_artifact"].unit_traces[0]
    assert "temporary rate limit" in trace.rounds[0].stop_reason
    if recover:
        assert result["fact_check_status"] == "accepted"
    else:
        assert [q.question_id for q in quiz.questions] == ["unaffected"]
        assert quiz.version == 2
        assert any(i.category == "coverage" and "指定 2 题" in i.message for i in result["fact_check_issues"])


def _claim():
    return FactCheckClaim(claim_id="claim", artifact_id="content", knowledge_unit_id="unit",
        target_id="target", role="explanation", kind="fact", text="assertion", query="query")


def test_unproved_contradiction_does_not_invent_external_conflict():
    class Provider:
        def generate_structured(self, request):
            value = request.schema.model_validate({"assessments": [{"claim_id": "claim", "verdict": "contradicted",
                "judgement": "unproved", "sufficient_textbook_evidence": False}]})
            return SimpleNamespace(value=value)
    external = FactCheckEvidence(evidence_id="web", source_kind="web", provider="test", locator="url", text="some text")
    checked, error = _assess_claims(Provider(), claims=[_claim()], evidence_by_claim={"claim": ([], [external], [])},
        content_artifact_id="content", quiz_artifact_id=None, round_number=0)
    assert error is None
    assert checked[0].verdict == "insufficient"


def test_programming_errors_are_logged_and_are_not_labeled_missing_evidence(caplog):
    class Provider:
        def generate_structured(self, request):
            raise AttributeError("broken assessment adapter")
    checked, error = _assess_claims(Provider(), claims=[_claim()], evidence_by_claim={"claim": ([], [], [])},
        content_artifact_id="content", quiz_artifact_id=None, round_number=0)
    assert "AttributeError:broken assessment adapter" in error
    assert error in checked[0].stop_reason
    assert "broken assessment adapter" in caplog.text
    issue = _issue_for_assessment(_fixture()[1], _claim(), checked[0])
    assert "核查未完成" in issue.message
    assert "证据不足" not in issue.message


def test_failed_textbook_reassessment_preserves_original_insufficient_verdict(caplog):
    class Provider(ScriptedFactCheckProvider):
        assessments = 0
        def generate_structured(self, request):
            if request.schema.__name__ == "FactCheckAssessmentsDraft":
                self.assessments += 1
                if self.assessments == 2:
                    raise AttributeError("reassessment bug")
            return super().generate_structured(request)
    fixture = _fixture()
    fixture[2].excerpts[0].text = "第一章 引言"
    provider = Provider(claim_target_index=0, claim_text="上确界是最小上界。", verdict="insufficient")
    result = _run(provider, fixture, additional=True)
    assessment = result["fact_check_artifact"].unit_traces[0].rounds[0].assessments[0]
    assert provider.assessments == 2
    assert assessment.verdict == "insufficient"
    assert "reassessment bug" in assessment.stop_reason
    assert "reassessment bug" in caplog.text


def test_schema_retry_without_candidate_uses_generation_prompt():
    provider = ScriptedContentProvider([ProviderError("invalid JSON", category="schema"),
                                       _draft(refs=["src-1"]), {"issues": []}])
    result = build_content_reflection_subgraph(provider, max_attempts=3).invoke(_state())
    assert result["route"] == "accept"
    assert provider.calls[1].metadata["agent"] == "generate_content_artifact"
    assert result["attempts"][1].generation_stage == "initial"
    assert result["attempts"][1].revision_prompt is None


@pytest.mark.parametrize("text", ["中文教材" * 100, "English textbook " * 100, r"中文 $\\frac{x}{y}$" * 100])
def test_budget_and_context_use_same_mixed_language_estimate(text, monkeypatch):
    monkeypatch.setenv("TOKEN_ESTIMATE_SCALE", "1.7")
    assert encoded_size(text) == estimate_tokens(text)
    assert estimate_tokens("中文" * 100) == 340
    assert estimate_tokens("英文" * 100) < len(("英文" * 100).encode("utf-8"))


def test_prompt_archive_deduplicates_and_roundtrips_without_mutating_runtime(tmp_path):
    prompt = "教材与完整 schema " * 1000
    payload = {"attempts": [{"generation_prompt": prompt, "revision_prompt": prompt},
                            {"generation_prompt": prompt, "critic_prompt": prompt}]}
    path = write_artifact_json(tmp_path / "trace.json", payload)
    assert payload["attempts"][0]["generation_prompt"] == prompt
    compact = json.loads(path.read_text(encoding="utf-8"))
    assert compact["attempts"][0]["generation_prompt"]["storage"] == "gzip-sha256-v1"
    assert len(list((tmp_path / "prompts").glob("*.gz"))) == 1
    assert read_artifact_json(path) == payload
    assert sum(p.stat().st_size for p in tmp_path.rglob("*") if p.is_file()) < len(json.dumps(payload)) / 10
    trace = ContentAttemptTrace(attempt=1, route="accept", generation_prompt=compact["attempts"][0]["generation_prompt"])
    assert trace.generation_prompt.sha256


def test_prompt_archive_keeps_legacy_strings_and_rejects_invalid_references(tmp_path):
    assert restore_prompts({"prompt": "legacy"}, tmp_path) == {"prompt": "legacy"}
    compact = archive_prompts({"prompt": "text" * 1000}, tmp_path)
    compact["prompt"]["path"] = "../outside.gz"
    with pytest.raises(ValueError, match="path"):
        restore_prompts(compact, tmp_path)


def test_offline_content_and_fact_check_use_the_provider_interface():
    from annotation.offline import OfflineFixtureProvider
    class RecordingOffline(OfflineFixtureProvider):
        calls = []
        def generate_structured(self, request):
            self.calls.append(request.schema.__name__)
            return super().generate_structured(request)
    provider = RecordingOffline()
    result = build_content_reflection_subgraph(provider, max_attempts=3).invoke(_state())
    assert result["route"] == "accept"
    _run(provider, _fixture())
    assert provider.calls == ["ContentDraft", "ContentCritiqueDraft", "FactCheckClaimsDraft"]
