"""Deployment layer: FastAPI service around the agent loop.

Endpoints:
  POST /run            {"goal": "...", "token_budget": 8000} -> terminal AgentState
  GET  /runs/{run_id}  inspect any checkpointed run (step-by-step audit trail)
  GET  /health
  GET  /metrics        aggregate runs, statuses, tokens, est. cost

The per-run token budget is clamped server-side to MAX_BUDGET so no client
can request an unbounded run: the cost ceiling is enforced by the server,
not trusted from the caller.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from agent.loop import AgentLoop
from agent.planner import get_planner
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
MAX_BUDGET = 12000
MAX_STEPS = 12

app = FastAPI(title="IT Asset Agent with Loop Design", version="1.0.0")
_planner = get_planner()
_lock = threading.Lock()
_metrics = {"started_at": time.time(), "runs": 0, "statuses": {}, "tokens": 0, "cost_usd": 0.0}


class RunRequest(BaseModel):
    goal: str = Field(min_length=5, max_length=500)
    token_budget: int = Field(default=8000, ge=200, le=MAX_BUDGET)
    max_steps: int = Field(default=10, ge=1, le=MAX_STEPS)


@app.post("/run")
def run_task(req: RunRequest):
    loop = AgentLoop(_planner)
    state = loop.run(req.goal, token_budget=req.token_budget, max_steps=req.max_steps)
    with _lock:
        _metrics["runs"] += 1
        _metrics["statuses"][state.status] = _metrics["statuses"].get(state.status, 0) + 1
        _metrics["tokens"] += state.tokens_used
        _metrics["cost_usd"] = round(_metrics["cost_usd"] + state.est_cost_usd, 6)
    return state.model_dump()


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    path = ROOT / "checkpoints" / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "run not found")
    return json.loads(path.read_text())


@app.get("/health")
def health():
    return {"status": "ok", "planner": getattr(_planner, "model", "unknown"),
            "max_token_budget": MAX_BUDGET}


@app.get("/metrics")
def metrics():
    with _lock:
        runs = _metrics["runs"]
        return {
            "uptime_s": round(time.time() - _metrics["started_at"], 1),
            "runs": runs,
            "terminal_statuses": _metrics["statuses"],
            "completion_rate": round(_metrics["statuses"].get("completed", 0) / runs, 3) if runs else 0.0,
            "total_tokens": _metrics["tokens"],
            "avg_tokens_per_run": round(_metrics["tokens"] / runs) if runs else 0,
            "total_est_cost_usd": _metrics["cost_usd"],
        }


from fastapi.responses import FileResponse


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(ROOT / "static" / "index.html")
