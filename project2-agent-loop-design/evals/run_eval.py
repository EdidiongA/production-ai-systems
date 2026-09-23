"""Agent evaluation suite: 10 bounded tasks, each with a programmatic checker.

A task passes only if BOTH the terminal status matches expectation AND the
checker validates the final answer (or, for failure-injection tasks, the loop
degraded the intended way). Per-run metrics: steps, tokens, est. cost, tool
errors survived.

Usage: python -m evals.run_eval
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.loop import AgentLoop
from agent.planner import get_planner

# expected ground truth VERIFIED against data/assets.db with direct SQL
# (first draft of these expectations was wrong: it counted acc-app-01, whose
#  status is 'unsupported_os', in an "active assets" total - the eval caught
#  the evaluator, which is documented in the README):
# active assets with warranty_end <= 2026-09-30 (same set for <= 2026-08-31):
#   lag-lap-014 (350), lag-db-01 (5600), lag-lap-022 (350),
#   lag-dc-01 (4200), lag-dc-02 (4200)  -> total 14700 USD
# NGN rate mocked at 1495.0 -> 14700*1495 = 21,976,500

TASKS = [
    {
        "id": "t01",
        "goal": "List active assets with warranty due on or before 2026-09-30 and the total renewal cost in USD.",
        "expect_status": "completed",
        "check": lambda a: "14700" in a and "lag-db-01" in a,
    },
    {
        "id": "t02",
        "goal": "What is the total renewal cost in USD for warranties due on or before 2026-08-31, converted to NGN?",
        "expect_status": "completed",
        # 350+5600+4200+4200+350 = 14700 USD; *1495 = 21976500
        # note: fx_lookup NGN times out on first call - agent must retry
        "check": lambda a: "14700" in a and "21976500" in a,
    },
    {
        "id": "t03",
        "goal": "Who can approve a hardware warranty renewal above the cost threshold, according to policy?",
        "expect_status": "completed",
        "check": lambda a: "Head of IT" in a and "5,000" in a,
    },
    {
        "id": "t04",
        "goal": "Count active assets by location.",
        "expect_status": "completed",
        "check": lambda a: "Lagos" in a and "6" in a,
    },
    {
        "id": "t05",
        "goal": "Which assets have an unsupported OS and must be flagged per security policy?",
        "expect_status": "completed",
        "check": lambda a: "acc-app-01" in a,
    },
    {
        "id": "t06",
        "goal": "What is the FY2026 renewal budget in the budget file?",
        "expect_status": "completed",
        "check": lambda a: "25000" in a,
    },
    {
        "id": "t07",
        "goal": ("For warranties due on or before 2026-09-30, is the total renewal cost within "
                 "the FY2026 renewal budget, and who must approve renewals above the policy threshold?"),
        "expect_status": "completed",
        "check": lambda a: "14700" in a and "25000" in a and "Head of IT" in a,
    },
    {
        "id": "t08",
        # failure injection: XOF is not served by the FX mock -> repeated 404
        "goal": "Convert 1000 USD to XOF.",
        "expect_status": "tool_error_streak",
        "check": lambda a: True,  # graceful stop IS the success criterion
    },
    {
        "id": "t09",
        # failure injection: malformed model output on first turn
        "goal": "[chaos:malformed] Count active assets by location.",
        "expect_status": "completed",
        "check": lambda a: "Lagos" in a,
    },
    {
        "id": "t10",
        # budget ceiling: absurdly low token budget must trip the cost stop
        "goal": "List active assets with warranty due on or before 2026-09-30 and the total renewal cost in USD.",
        "expect_status": "budget_exceeded",
        "token_budget": 150,
        "check": lambda a: True,
    },
]


def main() -> int:
    planner = get_planner()
    results = []
    for task in TASKS:
        loop = AgentLoop(planner)
        state = loop.run(task["goal"], token_budget=task.get("token_budget", 8000))
        answer = state.final_answer or ""
        passed = state.status == task["expect_status"] and task["check"](answer)
        results.append({
            "id": task["id"], "goal": task["goal"][:80],
            "status": state.status, "expected_status": task["expect_status"],
            "passed": passed, "steps": len(state.steps),
            "tokens": state.tokens_used, "est_cost_usd": state.est_cost_usd,
            "tool_errors_survived": sum(1 for s in state.steps if s.ok is False),
            "answer": answer[:180], "run_id": state.run_id,
        })

    n = len(results)
    passed = sum(r["passed"] for r in results)
    summary = {
        "model": getattr(planner, "model", "unknown"),
        "tasks": n, "passed": passed, "pass_rate": round(passed / n, 3),
        "avg_steps": round(statistics.mean(r["steps"] for r in results), 1),
        "avg_tokens_per_task": round(statistics.mean(r["tokens"] for r in results)),
        "total_est_cost_usd": round(sum(r["est_cost_usd"] for r in results), 6),
        "failure_injection_tasks": ["t08", "t09", "t10"],
    }
    report = {"summary": summary, "results": results}
    (ROOT / "evals" / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(summary, indent=2))
    for r in results:
        flag = "PASS" if r["passed"] else "FAIL"
        print(f"  [{flag}] {r['id']} status={r['status']} steps={r['steps']} tokens={r['tokens']}")
        if not r["passed"]:
            print(f"         answer: {r['answer']}")
    return 0 if passed == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
