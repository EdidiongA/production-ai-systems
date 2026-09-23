# Project 1: RAG System with Failure Analysis

> **The real-world problem.** Off-grid solar is one of the fastest-growing energy segments in West Africa, and the guidance gap is dangerous: a LiFePO4 bank costs $2,000–4,000 and a single wrong charge-voltage answer can destroy it; an undersized fuse is a fire risk. Installers and owners ask exactly the questions in this eval set. A system serving this domain must **cite its sources and abstain when unsure**: a fluent guess is worse than no answer. That requirement, not the happy path, drives every design decision below.

## Interface

![Helio - Solar Advisory RAG console](../assets/ui-helio.png)

*The production console: cited answer with confidence, retrieved-context grounding panel, and live guardrail status. Abstention is a first-class outcome, the recent-queries rail shows an out-of-corpus question the system declined rather than guessed.*

A question-answering system over a home solar-power engineering corpus (panel sizing, hybrid inverters, LiFePO4 storage, wiring protection, generator integration, monitoring, economics, a domain I know from running a 5.6 kWp / 5.5 kVA / 18 kWh system in Lagos), with an evaluation harness that separates retrieval failures from generation failures from citation mismatches.

**Problem statement:** a system that answers technical solar-installation questions from a curated 8-document corpus, where success means ≥70% of a hand-written 24-query eval set is answered correctly with a verifiable citation, and unanswerable questions are refused rather than hallucinated.

## Architecture

```
question ──> BM25 retriever (top-4 paragraph chunks)
                 │
                 v
         prompt = context chunks tagged [chunk_id=...] + question
                 │
                 v
         LLM (Anthropic API if ANTHROPIC_API_KEY set, else deterministic
              extractive mock) ──> raw JSON
                 │
                 v
         Pydantic validation (RAGAnswer) + 1 repair retry
                 │
                 v
         citation guardrail: citations filtered to actually-retrieved chunks;
         uncited non-abstained answers are downgraded to abstention
                 │
                 v
         JSONL request log + rolling cost/latency metrics
```

Files: `rag/chunker.py`, `rag/retriever.py`, `rag/llm.py`, `rag/schemas.py`, `rag/pipeline.py`, `app.py` (FastAPI), `evals/` (eval set, harness, regression tests).

## The four signals

| Signal | Where |
|---|---|
| Retrieval | `rag/retriever.py` - pure-Python BM25 over paragraph chunks |
| Structured output | `rag/schemas.py` - every answer validated against `RAGAnswer` (answer, citations, confidence, abstained); malformed output triggers one repair retry, then a logged `SchemaViolation` |
| Evaluation | `evals/run_eval.py` - 24 hand-written queries, six-way failure taxonomy, `evals/test_regression.py` - 5 pytest gates |
| Deployment | `app.py` + `Dockerfile` - FastAPI with `/ask`, `/health`, `/metrics`, rate limiting, request logging, per-request cost estimation |

## Design decisions I can defend

1. **Paragraph chunks, not fixed-token windows.** The corpus is reference material where each paragraph is a self-contained claim cluster. Fixed windows split numeric claims ("55.2–56.0 V") from their qualifiers ("for a 48 V/16S bank"), which manufactures citation mismatches.
2. **BM25, not a vector DB.** <100 chunks, heavily numeric text full of exact tokens ("Class T", "LiFePO4", "F07") where lexical match beats generic embeddings without fine-tuning, and BM25 is deterministic, so the failure analysis is reproducible. The `Retriever` interface is swappable; an embedding retriever with the same `.search()` signature is the designed A/B upgrade.
3. **Citations are constrained to retrieved chunk_ids** at the pipeline level, not trusted from the model. An answer that cites nothing valid is downgraded to abstention rather than served.
4. **Top-k = 4.** k=2 dropped retrieval hit rate on comparison queries; k=8 doubled input tokens (cost) for no accuracy gain on this eval set.

## Evaluation results (mock backend, deterministic)

```
n_cases: 24         accuracy: 0.792 (19/24)
retrieval_hit_rate: 1.000
outcomes: correct 18, correct_abstention 1, generation_failure 5
avg latency: 0.36 ms   p95: 0.41 ms   (local mock; expect 800–2000 ms live)
avg tokens/query: 325 in / 62 out
```

Run it yourself: `python -m evals.run_eval` then `pytest evals/test_regression.py -q`.

## Where this breaks and why

1. **Paraphrase and diagnostic queries are the dominant failure mode (5/5 failures).** q12 ("Why is voltage a bad way to estimate state of charge?") and q21 ("My yield dropped 10 percent, what should I suspect?") share almost no vocabulary with the answering sentences ("discharge curve is flat", "soiling or a failed string"). Lexical retrieval still found the right *document* (hit rate 1.0), but the extractive generator scored the wrong sentence or abstained. Diagnosis: the failure is in generation-side evidence selection, not retrieval, the taxonomy is what shows this. Fix path: semantic reranking of sentences, or a live LLM backend (which reads meaning, not tokens).
2. **Comparative questions pick the thematic sentence over the numeric one.** q13 (inverter AC savings) returns the "highest-leverage upgrade" sentence instead of the "3–4.5 kWh, a 40–55 percent reduction" sentence - both are in the same retrieved chunk. This is exactly the "it gave me an answer" vs "it gave me a good answer" gap.
3. **Fixed during development (documented because the diagnosis matters):**
   - Sentence splitting on `:` truncated "bulk/absorb at 55.2–56.0 V" answers (found via eval q10; fixed in `rag/llm.py`).
   - The unanswerable canary q24 ("Deye warranty period") originally got a confident false answer because "5.5 kVA inverter" gave high lexical overlap. Fixed with a question-term coverage guard (<0.55 coverage ⇒ abstain). Tradeoff: the same guard is what now over-abstains on q12/q21 - abstention precision was bought with paraphrase recall. That tradeoff is tunable, and the regression suite pins the floor while tuning.
4. **Multi-document questions** ("compare generator cost per kWh to grid Band A tariff") span docs 06 and 08; top-4 usually captures both, but the extractive generator can only cite one sentence, so half the comparison is silently dropped.

## Cost awareness

Mock backend: $0. Live estimates at avg 325 input / 62 output tokens per query:

| Model | $/1M in | $/1M out | est. $/1,000 queries |
|---|---|---|---|
| claude-haiku-4-5 | 1.00 | 5.00 | **$0.64** |
| claude-sonnet-4-6 | 3.00 | 15.00 | $1.91 |

`/metrics` tracks real totals in production. Token counts are estimated at 4 chars/token offline; the live client can be switched to exact usage from the API response.

## Run it

```bash
pip install -r requirements.txt
python -m evals.run_eval                  # eval + failure report -> evals/report.json
pytest evals/test_regression.py -q       # regression gates
uvicorn app:app --port 8000              # serve
curl -X POST localhost:8000/ask -H 'Content-Type: application/json' \
     -d '{"query":"What bulk charge voltage for a 48V LiFePO4 bank?"}'
```

Docker: `docker build -t solar-rag . && docker run -p 8000:8000 -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY solar-rag`

## Limitations

- Corpus is 8 documents / 40 chunks; BM25's advantages shrink and embedding retrieval wins as the corpus grows past a few thousand chunks.
- The mock backend is extractive - it can never synthesize across chunks; live-model runs will shift the failure distribution toward citation mismatch (which the harness already classifies).
- Eval keyphrase matching is string containment; a semantically-correct rephrasing would be scored as a failure (conservative bias, acceptable for regression gating).
- Token/cost figures for the live path are estimates until wired to the API's returned `usage` block.
