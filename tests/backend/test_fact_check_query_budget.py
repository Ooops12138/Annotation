from __future__ import annotations

import pytest

from annotation.domain.artifacts import FactCheckAssessment, FactCheckClaim, FactCheckEvidence, FactCheckPolicy
from annotation.workflow import fact_check as fc
from annotation.workflow.models import FactCheckRetrievalDecision
from test_fact_check_loop import _fixture


@pytest.mark.parametrize("budget,insufficient,correction,failed", [
    (0, False, False, False), (1, False, False, False),
    (2, False, False, False), (3, True, False, False),
    (1, True, False, True), (1, False, True, False),
])
def test_all_queries_share_unit_budget(monkeypatch, budget, insufficient, correction, failed):
    blueprint, task, pack, content, _, _ = _fixture()
    textbook_calls, web_calls, snapshots = [], [], []
    claims = [FactCheckClaim(claim_id=f"c{i}", artifact_id=content.artifact_id,
        knowledge_unit_id=task.knowledge_unit_id, target_id=content.artifact_id,
        role="explanation", kind="fact", text=f"claim{i}", query=f"follow{i}") for i in range(2)]
    monkeypatch.setattr(fc, "_extract_claims", lambda *a, **kw: (claims, None))
    monkeypatch.setattr(fc, "_fact_check_retrieval_decision", lambda *a, **kw:
        FactCheckRetrievalDecision(action="search_both", textbook_queries=["t1", "t2", "t3"],
                                   web_queries=["w1", "w2"]))

    def evidence(kind, query):
        return FactCheckEvidence(evidence_id=f"{kind}-{query}", source_kind=kind,
            provider="test", locator="same", text="same excerpt", source_ref="src-1" if kind == "textbook" else None)

    def textbook(skill, claim, *args):
        textbook_calls.append(claim.query)
        return [evidence("textbook", claim.query)], "textbook:success"

    def web(skill, claim):
        web_calls.append(claim.query)
        return ([], "web:error:temporary") if failed else ([evidence("web", claim.query)], "web:success")

    def assess(provider, *, claims, evidence_by_claim, round_number, **kwargs):
        snapshots.append(evidence_by_claim.copy())
        verdict = "contradicted" if correction and round_number == 0 else "insufficient" if insufficient else "supported"
        return [FactCheckAssessment(assessment_id=c.claim_id, claim_id=c.claim_id,
            verdict=verdict, judgement="test", route="revise_content" if verdict == "contradicted" else "annotate" if insufficient else "accept",
            textbook_evidence=evidence_by_claim[c.claim_id][0], external_evidence=evidence_by_claim[c.claim_id][1],
            stop_reason=";".join(evidence_by_claim[c.claim_id][2])) for c in claims], None

    monkeypatch.setattr(fc, "_search_textbook", textbook)
    monkeypatch.setattr(fc, "_search_web", web)
    monkeypatch.setattr(fc, "_assess_claims", assess)
    monkeypatch.setattr(fc, "_correct_content", lambda *a, **kw: (content, None))
    result = fc._check_unit(object(), task=task, unit=blueprint.knowledge_units[0],
        context_pack=pack, content=content, quiz=None,
        policy=FactCheckPolicy(web_enabled=True, web_query_limit=budget, max_corrections_per_unit=1),
        valid_source_refs={"src-1"}, textbook_skill=object(), web_skill=object(), additional_retrieval_enabled=True)
    assert textbook_calls[:6] == ["t1", "t2", "t3"] * 2
    assert len(web_calls) == budget
    assert len(snapshots[0]["c0"][0]) == 2  # context and one deduplicated search excerpt
    assert len(snapshots[0]["c0"][1]) == (1 if budget and not failed else 0)
    assert any("web:budget_exhausted" in (a.stop_reason or "") for r in result[2].rounds for a in r.assessments)
    if correction:
        assert len(result[2].rounds) == 2


def test_query_normalization_and_evidence_identity():
    assert fc._unique([" one ", "one", "", "two"]) == ["one", "two"]
    first = FactCheckEvidence(evidence_id="a", source_kind="web", provider="test", locator="url", text="excerpt")
    duplicate = first.model_copy(update={"evidence_id": "b"})
    distinct = first.model_copy(update={"text": "different excerpt"})
    assert fc._merge_evidence([first], [duplicate, distinct]) == [first, distinct]
