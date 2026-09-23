"""Regression gates: any pipeline change (chunking, prompt, top_k, retriever)
must keep these invariants. Run with:  pytest evals/test_regression.py -q
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.pipeline import RAGPipeline

import evals.run_eval as harness


@pytest.fixture(scope="module")
def results():
    pipeline = RAGPipeline(ROOT / "data" / "docs")
    harness._PIPELINE = pipeline
    cases = json.loads((ROOT / "evals" / "eval_set.json").read_text())["cases"]
    out = []
    for case in cases:
        qr = pipeline.ask(case["query"])
        out.append((case, harness.classify(case, qr), qr))
    return out


def test_overall_accuracy_floor(results):
    ok = sum(o in ("correct", "correct_abstention") for _, o, _ in results)
    assert ok / len(results) >= 0.70, "accuracy regressed below the 70% floor"


def test_retrieval_hit_rate_floor(results):
    misses = sum(o == "retrieval_failure" for _, o, _ in results)
    assert misses / len(results) <= 0.20, "retrieval failures exceed 20%"


def test_no_false_answers_on_unanswerable(results):
    for case, outcome, _ in results:
        if not case["answerable"]:
            assert outcome == "correct_abstention", (
                f"{case['id']} answered an unanswerable question"
            )


def test_every_answer_carries_valid_citation(results):
    for case, _, qr in results:
        if not qr.result.abstained:
            assert qr.result.citations, f"{case['id']}: non-abstained answer with no citation"
            assert set(qr.result.citations) <= set(qr.retrieved_chunk_ids), (
                f"{case['id']}: cited a chunk that was never retrieved"
            )


def test_direct_lookup_cases_stay_correct(results):
    """The easiest tier must never regress - these are the canary cases."""
    canaries = {"q01", "q05", "q10", "q15", "q18"}
    for case, outcome, _ in results:
        if case["id"] in canaries:
            assert outcome == "correct", f"canary {case['id']} regressed: {outcome}"
