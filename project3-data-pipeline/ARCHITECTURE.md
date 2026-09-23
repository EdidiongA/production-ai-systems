# Architecture — Mini Data Pipeline for AI-Ready Data

## System architecture

```
 data/raw/*.csv (3 batches, deliberately messy, seeded generator)
        │
        ▼
┌───────────────────────────── pipeline/ingest.py ──────────────────────────────┐
│ per-batch COLUMN MAPPING (schema drift handled explicitly, fails loudly)      │
│   -> normalizers: date(x4 formats, DMY/MDY hints) · cost->USD(pinned rates)   │
│      priority · status · office · category · mojibake repair                  │
│   -> Pydantic Ticket gate (id pattern, literals, cost>=0, real calendar date) │
│        ├─ ACCEPT -> SQLite tickets (indexed)                                  │
│        ├─ REJECT -> logs/rejected.jsonl   (reason + FULL raw record)          │
│        └─ every field change -> logs/transformed.jsonl                        │
│ conservation asserts: accepted+rejected == parsed rows;                       │
│ physical blank-line reconciliation (csv.DictReader skip-gap closed)           │
│ per-run quality score + reject-reason breakdown -> ingest_runs table          │
└────────────────────────────────────┬───────────────────────────────────────────┘
                                     ▼
                     data/processed/tickets.db (validated store)
                          │                         │
                ┌─────────▼──────────┐   ┌──────────▼─────────────────┐
                │ TicketRetriever    │   │ Summarizer                 │
                │ BM25 over subject+ │   │ SQL aggregates -> LLM      │
                │ category+office+.. │   │ (Anthropic | mock) ->      │
                └─────────┬──────────┘   │ Pydantic TicketSummary     │
                          │              └──────────┬─────────────────┘
 Browser ─ GET / ──> ┌────▼─────────────────────────▼────┐
 Client ─ /query ──> │ FastAPI (app.py) + dashboard UI   │
        ─ /summary ─>│ /ingest /quality /query /summary  │
        ─ /ingest ──>└───────────────────────────────────┘
```

## File structure

```
project3-data-pipeline/
├── app.py                            # FastAPI + UI mount
├── static/index.html                 # quality dashboard UI
├── pipeline/
│   ├── generate_messy_data.py        # seeded generator (reproducible mess)
│   └── ingest.py                     # mapping, normalizers, gate, logs, score
├── consumer/qa.py                    # TicketRetriever (BM25) + TicketSummary (LLM)
├── data/
│   ├── raw/                          # batch1_lagos / batch2_accra / batch3_freetown
│   └── processed/tickets.db
├── evals/
│   ├── run_eval.py                   # 15 checks: gates, naive-vs-pipeline, consumer
│   └── test_pipeline_invariants.py   # 7 pytest gates incl. normalizer unit tests
├── logs/                             # rejected.jsonl, transformed.jsonl
├── requirements.txt
└── Dockerfile
```

## Database schema

**tickets** (canonical store):

| column | type | constraint (enforced by Pydantic gate) |
|---|---|---|
| ticket_id | TEXT PK | `^[A-Z]{3}-\d{4}$`, deduplicated |
| opened_date | TEXT | ISO, real calendar date |
| office | TEXT | Lagos / Accra / Freetown |
| category | TEXT | hardware/software/network/access/printer/email/other |
| priority | TEXT | P1–P4 |
| cost_usd | REAL | ≥ 0, converted at pinned rates |
| status | TEXT | open / in_progress / resolved / closed |
| subject | TEXT | 1–200 chars, mojibake-repaired |
| source_batch, ingest_run | TEXT | provenance |

Indexes: `(status, priority)`, `(office)` — the consumer's hot filters.

**ingest_runs** (the monitoring table): run_id, ts, raw_rows, blank_rows,
accepted, rejected, transformed_fields, quality_score, reject_reasons (JSON),
elapsed_ms. One row per run → quality becomes a time series.

## API endpoints

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/` | — | dashboard UI | — |
| POST | `/ingest` | — | run report (score, reasons, counts) | — |
| GET | `/quality` | — | last 20 run reports (drift view) | 404 no runs |
| GET | `/query` | `q: str 2..200, top_k≤20` | validated matching tickets + scores | 404 no data, 422 |
| GET | `/summary` | — | schema-validated `TicketSummary` | 404 no data |
| GET | `/health` | — | status, db_exists | — |

## UI architecture

Single static dashboard. Quality card (metric grid, score bar with `role=img`
label, reject-reason chips, run history sparkline-as-text, re-ingest button
with busy state) + search card (results table over validated tickets). States:
loading, no-runs-yet empty state that points at the ingest action, no-results,
endpoint-unavailable, network error. Escaped output, labeled controls,
focus-visible outlines, reduced-motion support.
