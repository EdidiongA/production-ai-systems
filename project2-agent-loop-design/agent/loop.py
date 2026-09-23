"""Agent loop with explicit state, budget, checkpointing, and stop conditions.

What makes this a *designed loop* rather than "an LLM in a while True":

  State model      AgentState is an explicit, serializable record: goal,
                   step history, tokens spent, tool-error streak.
  Checkpointing    state is written to checkpoints/<run_id>.json after every
                   step; a crashed run can be inspected or resumed.
  Cost ceiling     hard token budget per run; exceeding it stops the loop
                   with status=budget_exceeded regardless of model intent.
  Stop conditions  (1) finish action that PASSES the goal validator,
                   (2) max_steps, (3) token budget, (4) three consecutive
                   tool errors, (5) two consecutive schema violations.
                   "The model says it is done" alone is NOT sufficient: a
                   finish without evidence citing an observed step is
                   rejected and the loop continues.
  Step logging     every decision, tool call, and observation is appended to
                   logs/steps.jsonl.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.registry import TOOLS, ToolResult

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------- structured actions
class ToolAction(BaseModel):
    type: Literal["tool"]
    thought: str = Field(max_length=500)
    tool_name: str
    tool_args: dict


class FinishAction(BaseModel):
    type: Literal["finish"]
    thought: str = Field(max_length=500)
    answer: str = Field(min_length=1, max_length=2000)
    evidence_steps: list[int] = Field(
        description="indices of prior steps whose observations support the answer"
    )


def parse_action(raw: str) -> ToolAction | FinishAction:
    data = json.loads(raw)
    if data.get("type") == "finish":
        return FinishAction.model_validate(data)
    return ToolAction.model_validate(data)


# --------------------------------------------------------------- state model
class StepRecord(BaseModel):
    index: int
    action_type: str
    thought: str = ""
    tool_name: str | None = None
    tool_args: dict | None = None
    observation: str | None = None
    ok: bool | None = None
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: float = 0.0


class AgentState(BaseModel):
    run_id: str
    goal: str
    status: Literal[
        "running", "completed", "max_steps", "budget_exceeded",
        "tool_error_streak", "schema_violation",
    ] = "running"
    steps: list[StepRecord] = Field(default_factory=list)
    tokens_used: int = 0
    token_budget: int = 8000
    max_steps: int = 10
    final_answer: str | None = None
    error_streak: int = 0
    schema_failures: int = 0
    est_cost_usd: float = 0.0


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------- the loop
class AgentLoop:
    def __init__(self, llm, checkpoint_dir: Path | None = None,
                 log_path: Path | None = None,
                 price_per_mtok: tuple[float, float] = (0.0, 0.0)):
        self.llm = llm
        self.checkpoint_dir = checkpoint_dir or ROOT / "checkpoints"
        self.log_path = log_path or ROOT / "logs" / "steps.jsonl"
        self.price = price_per_mtok

    def run(self, goal: str, token_budget: int = 8000, max_steps: int = 10) -> AgentState:
        state = AgentState(
            run_id=uuid.uuid4().hex[:12], goal=goal,
            token_budget=token_budget, max_steps=max_steps,
        )
        while state.status == "running":
            # stop condition: step ceiling
            if len(state.steps) >= state.max_steps:
                state.status = "max_steps"
                break
            # stop condition: cost ceiling
            if state.tokens_used >= state.token_budget:
                state.status = "budget_exceeded"
                break

            prompt = self._build_prompt(state)
            t0 = time.perf_counter()
            raw = self.llm.next_action(prompt, state)
            tokens_in = estimate_tokens(prompt)
            tokens_out = estimate_tokens(raw)
            state.tokens_used += tokens_in + tokens_out
            state.est_cost_usd = round(
                state.est_cost_usd
                + tokens_in / 1e6 * self.price[0]
                + tokens_out / 1e6 * self.price[1], 6)

            try:
                action = parse_action(raw)
                state.schema_failures = 0
            except (json.JSONDecodeError, ValidationError) as exc:
                state.schema_failures += 1
                self._log_step(state, StepRecord(
                    index=len(state.steps), action_type="schema_violation",
                    error=str(exc)[:200], tokens_in=tokens_in, tokens_out=tokens_out,
                    elapsed_ms=(time.perf_counter() - t0) * 1000))
                if state.schema_failures >= 2:
                    state.status = "schema_violation"
                self._checkpoint(state)
                continue

            if isinstance(action, FinishAction):
                # stop condition: finish must pass the goal validator -
                # evidence must reference at least one successful observation
                valid_refs = [
                    i for i in action.evidence_steps
                    if 0 <= i < len(state.steps) and state.steps[i].ok
                ]
                if not valid_refs:
                    self._log_step(state, StepRecord(
                        index=len(state.steps), action_type="finish_rejected",
                        thought=action.thought,
                        error="finish rejected: no valid evidence steps cited",
                        tokens_in=tokens_in, tokens_out=tokens_out,
                        elapsed_ms=(time.perf_counter() - t0) * 1000))
                    self._checkpoint(state)
                    continue
                state.final_answer = action.answer
                state.status = "completed"
                self._log_step(state, StepRecord(
                    index=len(state.steps), action_type="finish",
                    thought=action.thought, observation=action.answer, ok=True,
                    tokens_in=tokens_in, tokens_out=tokens_out,
                    elapsed_ms=(time.perf_counter() - t0) * 1000))
                self._checkpoint(state)
                break

            # tool action
            if action.tool_name not in TOOLS:
                result = ToolResult(tool=action.tool_name, ok=False, output="",
                                    error=f"unknown tool {action.tool_name}")
            else:
                fn, _ = TOOLS[action.tool_name]
                result = fn(action.tool_args)

            state.error_streak = 0 if result.ok else state.error_streak + 1
            self._log_step(state, StepRecord(
                index=len(state.steps), action_type="tool",
                thought=action.thought, tool_name=action.tool_name,
                tool_args=action.tool_args,
                observation=result.output[:800] if result.ok else None,
                ok=result.ok, error=result.error,
                tokens_in=tokens_in, tokens_out=tokens_out,
                elapsed_ms=result.elapsed_ms))
            # stop condition: repeated tool failure
            if state.error_streak >= 3:
                state.status = "tool_error_streak"
            self._checkpoint(state)
        self._checkpoint(state)
        return state

    def _build_prompt(self, state: AgentState) -> str:
        from tools.registry import TOOL_DESCRIPTIONS
        history = [
            {
                "step": s.index, "tool": s.tool_name, "args": s.tool_args,
                "ok": s.ok, "observation": s.observation, "error": s.error,
            }
            for s in state.steps
        ]
        return (
            f"GOAL: {state.goal}\n{TOOL_DESCRIPTIONS}\n"
            f"HISTORY: {json.dumps(history)}\n"
            'Reply with one JSON action: {"type":"tool","thought":...,'
            '"tool_name":...,"tool_args":{...}} or {"type":"finish",'
            '"thought":...,"answer":...,"evidence_steps":[...]}'
        )

    def _checkpoint(self, state: AgentState) -> None:
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        (self.checkpoint_dir / f"{state.run_id}.json").write_text(
            state.model_dump_json(indent=2))

    def _log_step(self, state: AgentState, record: StepRecord) -> None:
        state.steps.append(record)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as f:
            f.write(json.dumps({"run_id": state.run_id, **record.model_dump()}) + "\n")
