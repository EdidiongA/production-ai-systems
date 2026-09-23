# Architecture: RAG System with Failure Analysis

## System architecture

```
                       ┌─────────────────────────────────────────────┐
 Browser ── GET / ────>│  FastAPI (app.py)                           │
 Client ── POST /ask ─>│  rate limit (60/min) · thread-safe metrics  │
                       └───────────────┬─────────────────────────────┘
                                       │ RAGPipeline.ask(query)
                 ┌─────────────────────┼──────────────────────────┐
                 ▼                     ▼                          ▼
        ┌────────────────┐   ┌─────────────────┐    ┌─────────────────────┐
        │ BM25Retriever  │──>│ LLM backend     │───>│ Validation layer    │
        │ 40 chunks,     │   │ AnthropicLLM or │    │ Pydantic RAGAnswer  │
        │ top_k=4        │   │ MockLLM (+cov.  │    │ 1 repair retry      │
        └───────┬────────┘   │ guard 0.55)     │    │ citation guardrail  │
                │            └─────────────────┘    └──────────┬──────────┘
        ┌───────┴────────┐                                     │
        │ Chunker        │                          logs/requests.jsonl
        │ paragraph-level│                          (full observability
        │ stable chunk_ids                           record per query)
        └───────┬────────┘
        data/docs/*.md (8 solar engineering documents)
```

Request lifecycle: **question → BM25 retrieve top-4 chunks → prompt → generate →
schema-validate (repair once) → citation guardrail (cited ids ⊆ retrieved ids,
else downgrade to abstention) → JSONL log → response**.

## File structure

```
project1-rag-failure-analysis/
├── app.py                    # FastAPI serving + metrics + rate limit + UI mount
├── static/index.html         # query console UI
├── rag/
│   ├── chunker.py            # markdown -> paragraph chunks with stable ids
│   ├── retriever.py          # pure-Python BM25 (Retriever interface)
│   ├── llm.py                # AnthropicLLM | MockLLM (extractive + coverage guard)
│   ├── schemas.py            # RAGAnswer, QueryResult, SchemaViolation
│   └── pipeline.py           # orchestration + guardrails + logging
├── data/docs/                # 8-doc corpus (01_panel_sizing .. 08_economics)
├── evals/
│   ├── eval_set.json         # 24 queries: expect_keyphrases + expected_doc
│   ├── run_eval.py           # 6-way failure taxonomy classifier
│   └── test_regression.py    # 5 pytest gates (accuracy floor, canaries, ...)
├── logs/                     # requests.jsonl (runtime)
├── requirements.txt
└── Dockerfile
```

## Data schema

No database, the store is the chunk index (in-memory) plus append-only JSONL logs.

**Chunk** (in-memory): `chunk_id` (str, `"<doc>::p<n>"`), `doc` (str), `text` (str).

**RAGAnswer** (every response): `answer: str`, `citations: list[str]` (must be
retrieved chunk_ids), `confidence: float 0–1`, `abstained: bool`.

**Request log record** (`logs/requests.jsonl`): query, retrieved chunk_ids +
scores, raw model output, final validated answer, latency_ms, input/output
token estimates, est_cost_usd, timestamp.

## API endpoints

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/` | - | UI (HTML) | - |
| POST | `/ask` | `{query: str 3..500}` | `QueryResult` (RAGAnswer + latency, tokens, cost) | 422 invalid, 429 rate limit, 502 unrepairable schema violation |
| GET | `/health` | - | status, chunks_indexed, model | - |
| GET | `/metrics` | - | requests, abstention_rate, schema_violations, avg/p95 latency, cost totals, cost/1k | - |

## UI architecture

Single static page (`static/index.html`), zero dependencies, served by the API
itself. Three regions: ask form → answer card (`aria-live=polite`) → metrics
strip. States handled: loading (spinner, `prefers-reduced-motion` respected),
empty (pre-first-query), answered (citations + confidence), **abstained**
(distinct badge - abstention is a first-class outcome, not an error), rate-limited
(429 explained), validation error, network error. All dynamic text is
HTML-escaped before insertion; focus states and labels on all controls.
