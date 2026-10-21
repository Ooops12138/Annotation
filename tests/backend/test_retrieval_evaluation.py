"""Metric correctness, frozen labels, and failure accounting."""
import json
import math
from pathlib import Path

import pytest

from annotation.retrieval.evaluation import evaluate_tool, load_dataset, ranking_metrics
from annotation.fact_checking.contracts import SearchResult, SearchStatus

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/retrieval/math_ch1"


def test_metrics_against_hand_calculation():
    result = ranking_metrics(["irrelevant", "partial", "direct"], {"direct": 2, "partial": 1})
    assert result["hit@1"] == result["recall@1"] == result["mrr@1"] == result["ndcg@1"] == 0
    assert result["recall@3"] == 1
    assert result["mrr@3"] == .5
    expected = (1 / math.log2(3) + 3 / math.log2(4)) / (3 + 1 / math.log2(3))
    assert result["ndcg@3"] == pytest.approx(expected)
    assert ranking_metrics(["direct", "partial"], {"direct": 2, "partial": 1})["ndcg@10"] == 1
    with pytest.raises(ValueError):
        ranking_metrics(["direct", "direct"], {"direct": 2})


def test_frozen_corpus_and_question_labels():
    blocks, dataset = load_dataset(FIXTURE / "corpus.jsonl", FIXTURE / "questions.json")
    assert len(blocks) == 1206
    assert len({b.page_number for b in blocks}) == 25
    assert len(dataset["questions"]) == 48
    from collections import Counter
    counts = Counter(q["category"] for q in dataset["questions"])
    assert counts == {"keyword": 10, "paraphrase": 10, "conditions": 10, "comparison": 10, "unanswerable": 8}


def test_changed_corpus_or_qrel_hash_is_rejected(tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_bytes((FIXTURE / "corpus.jsonl").read_bytes() + b"\n")
    with pytest.raises(ValueError, match="different corpus"):
        load_dataset(corpus, FIXTURE / "questions.json")
    dataset = json.loads((FIXTURE / "questions.json").read_text(encoding="utf-8"))
    first_label = next(iter(dataset["questions"][0]["qrels"].values()))
    first_label["text_sha256"] = "wrong"
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps(dataset), encoding="utf-8")
    with pytest.raises(ValueError, match="qrel source"):
        load_dataset(FIXTURE / "corpus.jsonl", questions)


def test_errors_are_not_counted_as_successful_no_answer():
    blocks, dataset = load_dataset(FIXTURE / "corpus.jsonl", FIXTURE / "questions.json")
    questions = [dataset["questions"][0], dataset["questions"][-1]]
    class BrokenTool:
        def search(self, query, *, limit):
            return SearchResult(skill_name="test", skill_version="v1", query=query,
                                status=SearchStatus.ERROR, available=False)
    summary, rows = evaluate_tool(BrokenTool(), questions, blocks, repeats=3)
    assert summary["metrics"]["recall@5"] == 0
    assert summary["unanswerable_empty_rate"] == 0
    assert summary["error_rounds"] == 6
    assert len(rows) == 2
    assert summary["latency_ms"]["samples"] == 6
