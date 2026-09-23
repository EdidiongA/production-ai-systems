"""Planner backends.

AnthropicPlanner   live model via the Messages API (used when
                   ANTHROPIC_API_KEY is set)
MockPlanner        deterministic rule policy for offline dev/CI/evals.
                   It plans from goal keywords, reads the observation
                   history, retries failed tools up to twice, and emits one
                   deliberately malformed reply when the goal carries a
                   [chaos:malformed] tag - so the loop's schema-repair and
                   error-streak machinery is exercised by real eval tasks,
                   not by trust.
"""
from __future__ import annotations

import json
import os
import re
from ast import literal_eval


class AnthropicPlanner:
    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model
        self.api_key = os.environ["ANTHROPIC_API_KEY"]

    def next_action(self, prompt: str, state=None) -> str:
        import urllib.request

        body = json.dumps({
            "model": self.model,
            "max_tokens": 500,
            "system": ("You are a bounded IT-asset operations agent. "
                       "Reply with exactly one JSON action object and nothing else."),
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return "".join(b.get("text", "") for b in data.get("content", []))


class MockPlanner:
    model = "mock-policy-v1"

    def next_action(self, prompt: str, state) -> str:
        goal = state.goal
        gl = goal.lower()

        # chaos: emit malformed JSON exactly once at the start of the run
        if "[chaos:malformed]" in gl and not state.steps:
            return "SURE! Here is the plan I would take: {broken json"

        plan = self._build_plan(gl, goal)

        # execute plan: find first step without a successful observation
        for step in plan:
            done = self._find_ok(state, step["tool_name"], step.get("match"))
            if done is not None:
                continue
            fails = self._count_failures(state, step["tool_name"])
            if fails >= 2:
                # give up on this tool; try to finish with what we have,
                # or keep failing (loop's error-streak stop will fire)
                return self._retry_same(state)
            return json.dumps({"type": "tool", "thought": step["thought"],
                               "tool_name": step["tool_name"],
                               "tool_args": self._resolve_args(step, state)})

        return self._finish(state, gl)

    # ------------------------------------------------------------ plan build
    def _build_plan(self, gl: str, goal: str) -> list[dict]:
        plan: list[dict] = []
        date = None
        m = re.search(r"(\d{4}-\d{2}-\d{2})", goal)
        if m:
            date = m.group(1)

        if "unsupported" in gl:
            plan.append({"tool_name": "db_query", "match": "unsupported",
                         "thought": "Find assets flagged with unsupported OS",
                         "args": {"sql": "SELECT hostname, os, location FROM assets WHERE status='unsupported_os'"}})
        if any(w in gl for w in ("warranty", "renewal", "due", "expiring")) and date:
            plan.append({"tool_name": "db_query", "match": "renewal_cost_usd",
                         "thought": f"List active assets with warranty ending on/before {date}",
                         "args": {"sql": ("SELECT hostname, warranty_end, renewal_cost_usd FROM assets "
                                           f"WHERE status='active' AND warranty_end <= '{date}' "
                                           "ORDER BY warranty_end")}})
            if any(w in gl for w in ("total", "cost", "sum", "how much")):
                plan.append({"tool_name": "calculator", "match": "+",
                             "thought": "Sum the renewal costs",
                             "args": "SUM_FROM_DB"})
        if "count" in gl and "location" in gl:
            plan.append({"tool_name": "db_query", "match": "COUNT",
                         "thought": "Count assets grouped by location",
                         "args": {"sql": "SELECT location, COUNT(*) AS n FROM assets WHERE status='active' GROUP BY location ORDER BY n DESC"}})
        for cur in re.findall(r"\b(NGN|GHS|EUR|XOF|KES)\b", goal.upper()):
            plan.append({"tool_name": "fx_lookup", "match": cur,
                         "thought": f"Get the USD->{cur} rate",
                         "args": {"currency": cur}})
            plan.append({"tool_name": "calculator", "match": "*",
                         "thought": f"Convert the USD total to {cur}",
                         "args": "CONVERT_FX"})
        if any(w in gl for w in ("approval", "approve", "policy", "who can", "threshold")):
            plan.append({"tool_name": "kb_search", "match": None,
                         "thought": "Check the procurement policy on approvals",
                         "args": {"query": "renewal approval threshold Head of IT vendor quotes"}})
        if "budget" in gl:
            plan.append({"tool_name": "file_read", "match": "budget",
                         "thought": "Read the FY2026 budget file",
                         "args": {"filename": "budget_2026.txt"}})
        if not plan:  # fallback: treat as a policy question
            plan.append({"tool_name": "kb_search", "match": None,
                         "thought": "Search the knowledge base",
                         "args": {"query": goal[:120]}})
        return plan

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _find_ok(state, tool_name: str, match: str | None):
        for s in state.steps:
            if s.tool_name == tool_name and s.ok:  # noqa: SIM102 - kept nested for readability of the match rule
                if match is None or (
                    (match in (s.observation or "")) or
                    (match in json.dumps(s.tool_args or {}))
                ):
                    return s
        return None

    @staticmethod
    def _count_failures(state, tool_name: str) -> int:
        return sum(1 for s in state.steps if s.tool_name == tool_name and s.ok is False)

    def _retry_same(self, state) -> str:
        last = next((s for s in reversed(state.steps) if s.action_type == "tool"), None)
        if last is None:
            return json.dumps({"type": "tool", "thought": "retry",
                               "tool_name": "kb_search",
                               "tool_args": {"query": state.goal[:120]}})
        return json.dumps({"type": "tool",
                           "thought": f"Retrying {last.tool_name} after repeated failure",
                           "tool_name": last.tool_name,
                           "tool_args": last.tool_args or {}})

    def _db_rows(self, state):
        s = self._find_ok(state, "db_query", "renewal_cost_usd")
        if not s:
            return None
        try:
            return literal_eval(s.observation)
        except Exception:  # noqa: BLE001
            return None

    def _resolve_args(self, step: dict, state) -> dict:
        args = step["args"]
        if args == "SUM_FROM_DB":
            rows = self._db_rows(state) or []
            costs = [str(r.get("renewal_cost_usd", 0)) for r in rows] or ["0"]
            return {"expression": "+".join(costs)}
        if args == "CONVERT_FX":
            total = self._last_calc(state) or "0"
            rate = self._fx_rate(state) or "1"
            return {"expression": f"{total}*{rate}"}
        return args

    def _last_calc(self, state):
        for s in reversed(state.steps):
            if s.tool_name == "calculator" and s.ok:
                return s.observation
        return None

    def _fx_rate(self, state):
        for s in reversed(state.steps):
            if s.tool_name == "fx_lookup" and s.ok:
                m = re.search(r"=\s*([\d.]+)", s.observation or "")
                if m:
                    return m.group(1)
        return None

    # --------------------------------------------------------------- finish
    def _finish(self, state, gl: str) -> str:
        evidence = [s.index for s in state.steps if s.ok]
        parts = []
        rows = self._db_rows(state)
        if rows is not None:
            names = ", ".join(f"{r['hostname']} ({r['warranty_end']}, ${r['renewal_cost_usd']:.0f})"
                              for r in rows)
            parts.append(f"Assets due: {names}." if rows else "No matching assets.")
        count_step = self._find_ok(state, "db_query", "COUNT")
        if count_step:
            parts.append(f"Counts by location: {count_step.observation}")
        unsup = self._find_ok(state, "db_query", "unsupported")
        if unsup:
            parts.append(f"Unsupported-OS assets (critical finding per security policy): {unsup.observation}")
        calcs = [s for s in state.steps if s.tool_name == "calculator" and s.ok]
        if calcs:
            fx = self._fx_rate(state)
            if fx and len(calcs) >= 2:
                parts.append(f"Total renewal cost: {calcs[0].observation} USD "
                             f"= {calcs[-1].observation} at 1 USD = {fx}.")
            else:
                parts.append(f"Computed total: {calcs[-1].observation} USD.")
        kb = self._find_ok(state, "kb_search", None)
        if kb:
            parts.append(f"Policy: {kb.observation}")
        fr = self._find_ok(state, "file_read", None)
        if fr:
            m = re.search(r"renewal_budget_usd=(\d+)", fr.observation or "")
            if m:
                parts.append(f"FY2026 renewal budget: {m.group(1)} USD.")
        answer = " ".join(parts) or "Task completed; see step observations."
        return json.dumps({"type": "finish",
                           "thought": "All planned steps have observations; composing answer.",
                           "answer": answer, "evidence_steps": evidence})


def get_planner():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicPlanner(os.environ.get("AGENT_MODEL", "claude-sonnet-4-6"))
    return MockPlanner()
