"""Evaluation harness with failure taxonomy.

Classifies every eval case into exactly one outcome:

  correct              answer contains an expected keyphrase, retrieval hit,
                       and the cited chunk actually supports the answer
  correct_abstention   unanswerable case where the system abstained
  retrieval_failure    the expected source doc never appeared in top-k
                       (the generator never had a chance)
  generation_failure   retrieval succeeded but the answer is wrong or the
                       system abstained despite having the evidence
  citation_mismatch    the answer is right/plausible but the cited chunk
                       does not contain the supporting keyphrase
  false_answer         system answered an unanswerable question (hallucination
                       risk surface)

Usage:  python -m evals.run_eval  [--report evals/report.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.pipeline import RAGPipeline

ROOT = Path(__file__).resolve().parents[1]


def contains_any(text: str, phrases: list[str]) -> bool:
    t = text.lower()
    return any(p.lower() in t for p in phrases)


def classify(case: dict, qr) -> str:
    ans = qr.result
    if not case["answerable"]:
        return "correct_abstention" if ans.abstained else "false_answer"

    retrieval_hit = any(
        cid.split("#")[0] == case["expected_doc"] for cid in qr.retrieved_chunk_ids
    )
    if not retrieval_hit:
        return "retrieval_failure"

    answer_correct = contains_any(ans.answer, case["expect_keyphrases"])
    if not answer_correct:
        return "generation_failure"

    # citation support check: at least one cited chunk must contain a keyphrase
    chunk_texts = {c.chunk_id: c.text for c in _PIPELINE.chunks}
    supported = any(
        contains_any(chunk_texts.get(cid, ""), case["expect_keyphrases"])
        for cid in ans.citations
    )
    return "correct" if supported else "citation_mismatch"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(ROOT / "evals" / "report.json"))
    args = parser.parse_args()

    global _PIPELINE
    _PIPELINE = RAGPipeline(ROOT / "data" / "docs", log_path=ROOT / "logs" / "eval_queries.jsonl")

    cases = json.loads((ROOT / "evals" / "eval_set.json").read_text())["cases"]
    rows, latencies, costs, in_toks, out_toks = [], [], [], [], []
    for case in cases:
        qr = _PIPELINE.ask(case["query"])
        outcome = classify(case, qr)
        rows.append(
            {
                "id": case["id"],
                "query": case["query"],
                "tags": case["tags"],
                "outcome": outcome,
                "answer": qr.result.answer,
                "citations": qr.result.citations,
                "confidence": qr.result.confidence,
                "retrieved": qr.retrieved_chunk_ids,
                "expected_doc": case["expected_doc"],
                "latency_ms": qr.latency_ms,
            }
        )
        latencies.append(qr.latency_ms)
        costs.append(qr.est_cost_usd)
        in_toks.append(qr.input_tokens)
        out_toks.append(qr.output_tokens)

    outcomes = [r["outcome"] for r in rows]
    n = len(rows)
    summary = {
        "model": _PIPELINE.llm.model if hasattr(_PIPELINE.llm, "model") else "unknown",
        "n_cases": n,
        "accuracy": round(
            sum(o in ("correct", "correct_abstention") for o in outcomes) / n, 3
        ),
        "outcome_counts": {o: outcomes.count(o) for o in sorted(set(outcomes))},
        "retrieval_hit_rate": round(
            sum(o != "retrieval_failure" for o in outcomes) / n, 3
        ),
        "avg_latency_ms": round(statistics.mean(latencies), 2),
        "p95_latency_ms": round(sorted(latencies)[int(0.95 * (n - 1))], 2),
        "avg_input_tokens": round(statistics.mean(in_toks)),
        "avg_output_tokens": round(statistics.mean(out_toks)),
        "total_cost_usd": round(sum(costs), 6),
        "est_cost_per_1000_queries_usd": round(
            statistics.mean(costs) * 1000, 4
        ),
    }
    report = {"summary": summary, "cases": rows}
    Path(args.report).write_text(json.dumps(report, indent=2))

    print(json.dumps(summary, indent=2))
    print("\nFailures:")
    for r in rows:
        if r["outcome"] not in ("correct", "correct_abstention"):
            print(f"  [{r['outcome']}] {r['id']}: {r['query']}")
            print(f"      answer: {r['answer'][:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
