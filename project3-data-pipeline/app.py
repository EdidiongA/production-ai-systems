"""Deployment layer: FastAPI service over the pipeline and its consumer.

Endpoints:
  POST /ingest    re-run ingestion; returns the run report (quality score,
                  reject reasons) - the monitoring hook
  GET  /quality   quality score history across runs (drift becomes visible)
  GET  /query?q=  retrieval over validated tickets
  GET  /summary   structured, schema-validated executive summary
  GET  /health
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from consumer.qa import TicketRetriever, get_summary
from fastapi import FastAPI, HTTPException, Query
from pipeline.ingest import DB_PATH, run_ingestion

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="AI-Ready Data Pipeline", version="1.0.0")
_retriever: TicketRetriever | None = None


def _fresh_retriever() -> TicketRetriever:
    global _retriever
    if _retriever is None:
        _retriever = TicketRetriever()
    return _retriever


@app.post("/ingest")
def ingest():
    global _retriever
    report = run_ingestion()
    _retriever = None  # index rebuilt lazily on next query
    return report


@app.get("/quality")
def quality():
    if not DB_PATH.exists():
        raise HTTPException(404, "no ingest runs yet - POST /ingest first")
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT run_id, ts, raw_rows, accepted, rejected, transformed_fields, "
        "quality_score, reject_reasons FROM ingest_runs ORDER BY ts DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return [
        {"run_id": r[0], "ts": r[1], "raw_rows": r[2], "accepted": r[3],
         "rejected": r[4], "transformed_fields": r[5], "quality_score": r[6],
         "reject_reasons": json.loads(r[7])}
        for r in rows
    ]


@app.get("/query")
def query(q: str = Query(min_length=2, max_length=200), top_k: int = Query(5, ge=1, le=20)):
    if not DB_PATH.exists():
        raise HTTPException(404, "no data - POST /ingest first")
    return {"query": q, "results": _fresh_retriever().search(q, top_k=top_k)}


@app.get("/summary")
def summary():
    if not DB_PATH.exists():
        raise HTTPException(404, "no data - POST /ingest first")
    return get_summary().model_dump()


@app.get("/health")
def health():
    return {"status": "ok", "db_exists": DB_PATH.exists()}


from fastapi.responses import FileResponse


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(ROOT / "static" / "index.html")
