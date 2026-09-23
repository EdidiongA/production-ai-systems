# Project 2 — AI Agent with Loop Design

> **The real-world problem.** Multi-office organizations bleed money quietly: warranties lapse unnoticed, renewals get approved outside policy, and answering "what's due this quarter, in which currency, and is it within budget?" takes a person half a day across a spreadsheet, a policy PDF, and an FX site. An agent can do it in seconds — **if** it can be trusted: bounded spend, no fabricated numbers, and an audit trail a compliance officer can replay. Those three constraints are the actual engineering content of this project.

## Interface

![AssetPilot — agent run console](../assets/ui-assetpilot.png)

*A completed run: token-budget ring (2,503/8,000), the five stop conditions with live state, and the full execution trace — including step 4's injected FX timeout surfacing as data, the retry, and the evidence-backed finish citing steps 2, 3, 5, 6.*

A bounded IT-asset operations agent: it answers asset, warranty, cost, and policy questions by calling five typed tools over a SQLite asset inventory, a policy knowledge base, a calculator, a (deliberately flaky) FX-rate API, and a file reader. The point of the project is not the loop — it's everything built around the loop: an explicit state model, checkpointing, a hard token budget, five distinct stop conditions, step-level logging, and an eval suite that injects failures on purpose.

**Problem statement:** an agent that completes bounded asset-operations tasks (list/aggregate/convert/check-policy) from a 12-row asset database and 2-document policy KB, where success means ≥90% of a 10-task eval suite passes its programmatic checker, failure-injected runs terminate in the *designed* failure status, and no run exceeds its token ceiling.

## Loop design (the part that isn't "an LLM in a while True")

```
AgentState (explicit, serializable)                  Stop conditions
  goal, steps[], tokens_used, token_budget,           1. finish action that PASSES the
  error_streak, schema_failures, status                  goal validator (evidence_steps
                                                          must cite >=1 successful
  every step -> checkpoints/<run_id>.json                observation - "the model says
  every decision -> logs/steps.jsonl                     it's done" is NOT enough;
                                                          uncited finishes are rejected)
  loop:                                                2. max_steps ceiling
    check ceilings -> build prompt from state         3. token budget ceiling (checked
    -> planner returns ONE JSON action                    before every model call)
    -> Pydantic-validate (ToolAction|FinishAction)    4. 3 consecutive tool errors
    -> execute tool via typed registry                 5. 2 consecutive schema violations
    -> record observation -> checkpoint
```

## The four signals

| Signal | Where |
|---|---|
| Retrieval | `kb_search` tool — lexical retrieval over the policy knowledge base feeds policy context into the loop |
| Structured output | every model turn must be a JSON `ToolAction`/`FinishAction` (Pydantic); every tool has typed args and returns a typed `ToolResult` — malformed turns are logged as schema violations and the loop recovers or stops |
| Evaluation | `evals/run_eval.py` — 10 tasks with programmatic checkers, 3 of them failure-injection; `evals/test_loop_invariants.py` — 6 pytest invariants |
| Deployment | `app.py` + `Dockerfile` — `/run` (budget clamped server-side), `/runs/{id}` audit trail, `/metrics` |

## Design decisions I can defend

1. **The goal validator rejects unevidenced finishes.** A `finish` must cite `evidence_steps` pointing at successful observations; otherwise it's logged as `finish_rejected` and the loop continues. This is the difference between a stop condition and a vibe.
2. **Tools never raise into the loop.** Every tool returns `ToolResult(ok, output, error)`; error handling is data, not exceptions, so the planner can see and reason about failures — and the error-streak stop is computable.
3. **Cost ceiling enforced server-side.** The API clamps `token_budget` to 12,000; a client cannot request an unbounded run. The budget check runs *before* each model call, so overshoot is bounded by one turn.
4. **Failure modes are eval cases, not accidents.** The FX tool times out on its first NGN call by design; task t02 passes only if the agent retries and completes. Task t09 injects malformed model output; t10 sets a 150-token budget. If the recovery machinery breaks, the suite fails.

## Evaluation results (mock planner, deterministic)

```
tasks: 10   passed: 10   pass_rate: 1.00
avg_steps: 3.0   avg_tokens/task: 992
t02 (FX timeout injected):        completed in 6 steps, survived 1 tool error
t08 (unknown currency, 404 x3):   stopped as tool_error_streak, NO fabricated answer
t09 (malformed model output):     recovered via schema handling, completed
t10 (150-token budget):           stopped as budget_exceeded after 1 step
```

Run: `python -m evals.run_eval` and `pytest evals/test_loop_invariants.py -q`.

## Where this breaks and why

1. **The eval caught the evaluator.** The first draft of the ground truth counted `acc-app-01` (status `unsupported_os`) in an "active assets" renewal total — 18,600 USD instead of the correct 14,700. The agent was right; my hand-computed expectation was wrong. Hand-written eval sets need the same verification discipline as code (fixed by checking every expected number with direct SQL; the comment trail is kept in `evals/run_eval.py`).
2. **Plan-state tracking by tool name collides.** The mock planner initially marked the FX-conversion calculator step "done" because *a* calculator step (the USD sum) had succeeded — the agent finished with a USD answer to an NGN question. Diagnosed from `logs/steps.jsonl` (the step log showed 4 steps where the plan needed 5). Fixed by matching steps on argument signature, not tool name. The general lesson transfers to live models: dedupe/skip logic keyed on tool name alone silently drops repeated-tool plans.
3. **Graceful failure has no partial-credit path.** On three consecutive `fx_lookup` 404s (t08) the loop stops with `tool_error_streak` and *no* final answer. That's the designed behavior (never fabricate), but a production version should finish with a structured "here's what I could and couldn't do" partial answer. That requires letting a finish cite evidence for a *partial* goal — a validator design change, noted as future work.
4. **The mock planner is a keyword policy.** It cannot handle goals outside its five task families; the live `AnthropicPlanner` swaps in behind the same `next_action()` interface, and the same eval suite runs against it (expect schema violations > 0 and different step counts — which is what the suite is for).

## Cost awareness

Mock: $0. At the measured ~992 tokens/task (roughly 85% input / 15% output at avg 3 steps):

| Model | est. $/task | est. $/1,000 tasks |
|---|---|---|
| claude-haiku-4-5 | ~$0.0016 | ~$1.60 |
| claude-sonnet-4-6 | ~$0.0048 | ~$4.80 |

The hard token budget makes worst-case cost per run a computable number: `token_budget x price`, independent of model behavior. `/metrics` tracks actuals.

## Run it

```bash
pip install -r requirements.txt
python -m evals.run_eval
pytest evals/test_loop_invariants.py -q
uvicorn app:app --port 8000
curl -X POST localhost:8000/run -H 'Content-Type: application/json' \
  -d '{"goal":"Which assets have an unsupported OS and must be flagged per security policy?"}'
curl localhost:8000/runs/<run_id>      # full step-by-step audit trail
```

Docker: `docker build -t asset-agent . && docker run -p 8000:8000 -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY asset-agent`

## Limitations

- Read-only by design: no tool writes to any external system. A write-capable version needs the human-in-the-loop approval step described in the source article; the `finish_rejected` mechanism is where that gate would attach.
- Token counts are 4-chars/token estimates offline; the live planner should switch to API-reported usage.
- The state model is per-run only — no long-term memory between runs (intentional scope boundary).
- Checkpoints support inspection and post-mortems; resume-from-checkpoint is not implemented.
