# Architecture: AI Agent with Loop Design

## System architecture

```
 Browser ── GET / ─────>┌──────────────────────────────────────────────┐
 Client ── POST /run ──>│ FastAPI (app.py)                             │
        ── GET /runs/id>│ token budget CLAMPED server-side (≤12,000)   │
                        └──────────────────┬───────────────────────────┘
                                           │ AgentLoop.run(goal, budget)
                        ┌──────────────────▼───────────────────────────┐
                        │ AgentLoop (agent/loop.py)                    │
                        │  AgentState (Pydantic, serializable)         │
                        │  per-step checkpoint -> checkpoints/<id>.json│
                        │  ceilings checked BEFORE each model call     │
                        │  5 stop conditions:                          │
                        │   1 validated finish (evidence_steps must    │
                        │     cite successful observations)            │
                        │   2 max_steps  3 token budget                │
                        │   4 tool-error streak(3)  5 schema streak(2) │
                        └────────┬────────────────────────┬────────────┘
                     one JSON action                 execute action
                        ┌────────▼─────────┐   ┌──────────▼───────────────────┐
                        │ Planner          │   │ Tool registry (typed,        │
                        │ AnthropicPlanner │   │ NEVER raises into the loop)  │
                        │ or MockPlanner   │   │ calculator · db_query(SELECT │
                        │ (rule policy,    │   │ only) · kb_search · fx_lookup│
                        │ chaos injection) │   │ (flaky by design) · file_read│
                        └──────────────────┘   └──────────┬───────────────────┘
                                                          ▼
                                    data/assets.db · data/kb/*.md · data/files/
```

Run lifecycle: **goal → [check ceilings → planner proposes one validated
JSON action → execute tool → record ToolResult → checkpoint]\* → terminal
status**. Finishing is earned: an unevidenced `FinishAction` is rejected
(`finish_rejected`) and the loop continues.

## File structure

```
project2-agent-loop-design/
├── app.py                        # FastAPI /run /runs/{id} /metrics + UI mount
├── static/index.html             # run console UI (step trace viewer)
├── agent/
│   ├── loop.py                   # AgentLoop, AgentState, stop conditions, checkpoints
│   └── planner.py                # AnthropicPlanner | MockPlanner (+[chaos:malformed])
├── tools/registry.py             # 5 typed tools -> ToolResult(ok, output, error)
├── data/
│   ├── assets.db                 # 12-row SQLite inventory
│   ├── kb/                       # procurement_policy.md, security_policy.md
│   └── files/budget_2026.txt
├── evals/
│   ├── run_eval.py               # 10 tasks incl. 3 failure injections
│   └── test_loop_invariants.py   # 6 pytest invariants
├── checkpoints/  logs/           # runtime artifacts (audit trail)
├── requirements.txt
└── Dockerfile
```

## Database schema

`data/assets.db` - table **assets**:

| column | type | notes |
|---|---|---|
| id | TEXT PK | e.g. `lag-dc-01` |
| hostname | TEXT | |
| asset_type | TEXT | laptop / server / network |
| location | TEXT | Lagos / Accra / Freetown / Banjul |
| os | TEXT | |
| status | TEXT | active / retired / unsupported_os |
| warranty_end | TEXT | ISO date |
| renewal_cost_usd | REAL | |
| role | TEXT | |

Access is SELECT-only, enforced in the tool. Checkpoints (`checkpoints/*.json`)
are the serialized `AgentState`: goal, steps[] (action, args, observation, ok),
tokens_used, token_budget, error_streak, schema_failures, status, final_answer.

## API endpoints

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/` | - | UI (HTML) | - |
| POST | `/run` | `{goal: str 5..500, token_budget≤12000, max_steps≤12}` | terminal `AgentState` | 422 invalid |
| GET | `/runs/{run_id}` | - | checkpointed state (full audit trail) | 404 |
| GET | `/health` | - | status, planner model, max budget | - |
| GET | `/metrics` | - | runs, terminal-status distribution, completion_rate, tokens, est cost | - |

## UI architecture

Single static page. Goal form → result card with terminal-status badge
(`completed` green vs stopped amber, a stopped run is shown as a designed
outcome with its stop condition named, not as an error) → step-trace table
(per-step ok/fail dot, action, observation/error, `overflow-wrap` for long
observations). States: loading, empty, completed, stopped-without-answer,
validation error, network error. Escaped output, labeled controls, reduced-motion
support, responsive column clamping under 640px.
