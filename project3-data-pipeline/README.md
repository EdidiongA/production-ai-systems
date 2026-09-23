# Project 3: Mini Data Pipeline for AI-Ready Data

> **The real-world problem.** Every organization that wants "AI on our support data" has the same unglamorous blocker: the data is three different exports with three different schemas, mixed currencies, duplicate IDs, and encoding damage. Feed that to a model and it will *confidently* summarize nonsense - this repo quantifies exactly how wrong (343×). AI-readiness is a data-engineering property, and this pipeline makes it measurable: a quality score per run, a reason for every rejected record, and a downstream consumer whose numbers provably match ground truth.

## Interface

![Clearline - data quality dashboard](../assets/ui-clearline.png)

*The quality dashboard: per-run score with rejection-reason chips, the naive-load-vs-pipeline comparison (15,263,872 struck through against $44,538.42), and the validated store with provenance and an encoding-repair badge.*

A pipeline that ingests genuinely messy IT support-ticket exports from three regional office systems, validates and normalizes them into a canonical schema, stores them in SQLite, and feeds them into two downstream AI consumers - with a rejection log that makes every failure visible rather than silent, and a per-run quality score that turns data health into a monitorable number.

**Problem statement:** a system that turns three inconsistent ticket exports (120 raw rows with schema drift, four date formats, three currencies, duplicates, encoding damage, and invalid values) into an AI-ready ticket store, where success means zero silently-dropped rows, every injected corruption caught with a machine-readable reason, and a downstream summary whose numbers match SQL ground truth exactly.

## The mess (by design, seeded and reproducible)

| Batch | Problems |
|---|---|
| `batch1_lagos.csv` | dates DD/MM/YYYY; costs as "N45,000" / "45000 NGN" / bare / blank; priorities "P1"/"High"/"URGENT"/"low"; office spelled 4 ways; missing subjects |
| `batch2_accra.csv` | **schema drift**: entirely different column names (`id`, `date_opened`, `location`, `type`, `urgency`); ISO dates mixed with "Mar 3, 2026"; GHS and USD mixed in one column; "n/a" costs |
| `batch3_freetown.csv` | MM-DD-YYYY dates; an invalid calendar date (2026-02-30); a negative cost; duplicate ids within the batch **and** colliding with batch 1; a missing id; a status typo ("resolvd"); mojibake ("CafÃ©â€¦") from a latin-1 export; blank lines |

Regenerate identically: `python -m pipeline.generate_messy_data` (seeded RNG).

## Architecture

```
raw CSVs ──> per-batch column mapping (schema drift handled explicitly)
             ──> normalizers (date x4 formats, currency->USD, priority,
                  status, office, category, mojibake repair)
             ──> Pydantic Ticket validation (the schema gate)
        ├──> ACCEPT  -> SQLite tickets table
        ├──> REJECT  -> logs/rejected.jsonl  (reason + full raw record)
        └──> every field change -> logs/transformed.jsonl
             per-run quality score -> ingest_runs table (drift monitoring)

downstream consumers (the reason the data had to be clean):
  1. TicketRetriever - BM25 retrieval Q&A over validated tickets
  2. TicketSummary  - LLM-generated executive summary (Anthropic API when
     key present, deterministic mock otherwise), Pydantic-validated either way
```

## The four signals

| Signal | Where |
|---|---|
| Retrieval | `consumer/qa.py` - BM25 retrieval over validated tickets serves `/query` |
| Structured output | canonical `Ticket` model at ingestion; `TicketSummary` model on the LLM summary - downstream code never parses prose, and a schema-violating summary raises instead of serving |
| Evaluation | `evals/run_eval.py` - 15 checks in 3 layers (quality gates, naive-vs-pipeline comparison, consumer ground-truth match); `evals/test_pipeline_invariants.py` - 7 pytest gates incl. unit tests on every normalizer |
| Deployment | `app.py` + `Dockerfile` - `POST /ingest`, `GET /quality` (score history = drift monitor), `/query`, `/summary`, `/health` |

## Evaluation results

```
15/15 checks passed
ingest: 120 raw rows -> 102 accepted, 18 rejected, 402 field transforms
quality_score: 0.85 (persisted per run)
reject reasons: missing_subject 5, unknown_office 8, invalid calendar date 1,
                negative cost 1, duplicate id 1, missing id 1, bad date 1
```

**The number that justifies the project**: the same raw files loaded naively with pandas (what most notebooks do):

| | Naive pandas load | This pipeline |
|---|---|---|
| "Total cost" | **15,263,872** (NGN + GHS + USD summed as one column, a meaningless number that would go straight into an LLM prompt) | **44,538.42 USD** |
| Duplicate ids kept | 2 | 0 |
| Negative costs kept | yes | 0 (schema-rejected) |
| Invalid dates kept | yes | 0 |

A RAG app or dashboard built on the naive load would confidently report the nonsense total. Tracing that "hallucination" back to a malformed input record is exactly what `logs/rejected.jsonl` and `logs/transformed.jsonl` exist for.

## Design decisions I can defend

1. **Reject vs flag is per-defect, not global.** Structural defects (bad id, bad date, unknown office, negative cost) reject the record; recoverable defects (unparseable cost → 0.0, unknown category → "other", mojibake → repaired) flag-and-transform with a log entry. A blanket policy either destroys usable data or admits garbage.
2. **Schema drift is handled by explicit per-batch mappings**, not fuzzy column matching. Fuzzy matching fails silently when a source adds a column; an explicit mapping fails loudly, which is the correct failure.
3. **Currency normalization at ingestion with pinned rates** (documented in `FX_TO_USD`). The alternative - storing native currency and converting at query time - is more correct for accounting but pushes complexity into every consumer; for an AI-ready analytical store, one unit at ingestion is the right tradeoff. Rate staleness is a known limitation.
4. **Conservation as an assertion, not a hope:** `accepted + rejected == raw_rows` is asserted at the end of every run.

## Where this breaks and why

1. **`csv.DictReader` silently skips blank lines**: the stdlib itself has a silent-drop behavior. The injected blank lines never reached the pipeline, so `blank_rows` originally read 0 even though the file contained 2. **Now fixed**: physical data lines are counted per batch and reconciled, so the conservation invariant covers the file, not just what the reader yields (`blank_rows` correctly reads 2). Finding a silent drop *inside the tool used to prevent silent drops* remains the most instructive failure in this project.
2. **Rejected records don't reserve their IDs.** The first FNA-3005 was rejected (invalid date), so the second FNA-3005 was accepted as a first occurrence. Defensible (a rejected record arguably never existed) but it means dedup outcomes depend on rejection order, a re-ordered export changes which twin survives. A production version should key duplicates on all *seen* ids, accepted or not, and flag collisions with rejected records separately.
3. **Ambiguous date formats are resolved by per-batch hints**, not evidence. "03-05-2026" is March 5 under the Freetown MDY hint and May 3 under DMY - nothing in the value itself disambiguates. If a source system changes its locale, dates shift silently by up to 11 months and the quality score won't move. Mitigation: distribution checks (e.g., day-of-month > 12 frequency) per run.
4. **`unknown_office` (8 rejects) is the largest reject class and is genuinely ambiguous**: the raw rows have an empty office field but a valid FNA- prefix that *implies* Freetown. Inferring office from the id prefix would recover 8 records but couples validation to an id convention nobody promised. Chose strictness; documented the recoverable volume.
5. **Static FX rates make cost_usd a snapshot**, wrong the day rates move. Fine for a portfolio dataset; a production version needs dated rates and a rate-source column.

## Cost awareness

The pipeline itself is LLM-free - deterministic Python, ~23 ms for 120 rows, $0. The only model call is the `/summary` endpoint (one call per request, ~500 input / 250 output tokens): about **$0.0018 per summary on claude-haiku-4-5** ($1.75/1,000). Keeping the LLM out of the row-level path is itself the cost design: validating 120 rows with a model would cost real money and be *less* deterministic than the schema gate.

## Run it

```bash
pip install -r requirements.txt
python -m pipeline.generate_messy_data   # reproducible messy input
python -m pipeline.ingest                # -> data/processed/tickets.db + logs
python -m evals.run_eval                 # 15-check suite incl. naive comparison
pytest evals/test_pipeline_invariants.py -q
uvicorn app:app --port 8000
curl "localhost:8000/quality"            # quality score history
curl "localhost:8000/query?q=printer+offline"
curl "localhost:8000/summary"
```

Docker: `docker build -t ticket-pipeline . && docker run -p 8000:8000 ticket-pipeline`

## Limitations

- SQLite/single-process by scope (the brief is explicit: this is not a Spark project). The interfaces (mapping tables, normalizers, Pydantic gate) transfer to DuckDB/warehouse targets unchanged.
- Quality score is a single acceptance ratio; per-field completeness scores would catch subtler drift.
- The messy data is synthetic (seeded). It reproduces defect *classes* from real exports (Sage/service-desk CSVs), but real sources always contain one more surprise than you generated.
