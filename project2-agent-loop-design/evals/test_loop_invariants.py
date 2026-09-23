"""Loop invariants that must hold regardless of planner backend.
Run:  pytest evals/test_loop_invariants.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.loop import AgentLoop
from agent.planner import MockPlanner


def _run(goal, **kw):
    return AgentLoop(MockPlanner()).run(goal, **kw)


def test_budget_ceiling_is_hard():
    state = _run("List active assets with warranty due on or before 2026-09-30 "
                 "and the total renewal cost in USD.", token_budget=150)
    assert state.status == "budget_exceeded"
    # at most one action's tokens past the ceiling (checked before each call)
    assert state.tokens_used < 150 + 2000


def test_step_ceiling_is_hard():
    state = _run("Convert 1000 USD to XOF.", max_steps=2)
    assert state.status in ("max_steps", "tool_error_streak")
    assert len(state.steps) <= 2


def test_finish_requires_evidence():
    # every completed run's answer must be backed by >=1 successful step
    state = _run("Count active assets by location.")
    assert state.status == "completed"
    assert any(s.ok for s in state.steps)


def test_tool_error_streak_stops_loop():
    state = _run("Convert 1000 USD to XOF.")
    assert state.status == "tool_error_streak"
    assert state.final_answer is None  # no fabricated answer on failure


def test_malformed_output_recovers():
    state = _run("[chaos:malformed] Count active assets by location.")
    assert state.status == "completed"
    assert any(s.action_type == "schema_violation" for s in state.steps)


def test_checkpoint_written_per_run():
    state = _run("Count active assets by location.")
    assert (ROOT / "checkpoints" / f"{state.run_id}.json").exists()
