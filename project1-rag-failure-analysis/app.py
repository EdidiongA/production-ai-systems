"""Deployment layer: FastAPI service exposing the RAG pipeline.

Endpoints:
  POST /ask       {"query": "..."} -> validated RAGAnswer + observability fields
  GET  /health    liveness probe
  GET  /metrics   rolling cost / latency / abstention counters

Run:  uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import statistics
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from rag.pipeline import RAGPipeline
from rag.schemas import SchemaViolation

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Solar RAG with Failure Analysis", version="1.0.0")

_pipeline = RAGPipeline(ROOT / "data" / "docs", log_path=ROOT / "logs" / "requests.jsonl")
_lock = threading.Lock()
_metrics = {
    "started_at": time.time(),
    "requests": 0,
    "abstentions": 0,
    "schema_violations": 0,
    "latencies_ms": [],
    "total_cost_usd": 0.0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
}

# simple in-memory rate limit: max requests per rolling minute
RATE_LIMIT_PER_MIN = 60
_request_times: list[float] = []


class AskRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)


@app.post("/ask")
def ask(req: AskRequest):
    now = time.time()
    with _lock:
        _request_times[:] = [t for t in _request_times if now - t < 60]
        if len(_request_times) >= RATE_LIMIT_PER_MIN:
            raise HTTPException(429, "rate limit exceeded")
        _request_times.append(now)
    try:
        qr = _pipeline.ask(req.query)
    except SchemaViolation:
        with _lock:
            _metrics["schema_violations"] += 1
        raise HTTPException(502, "model returned unparseable output twice")
    with _lock:
        _metrics["requests"] += 1
        _metrics["abstentions"] += int(qr.result.abstained)
        _metrics["latencies_ms"].append(qr.latency_ms)
        _metrics["latencies_ms"] = _metrics["latencies_ms"][-1000:]
        _metrics["total_cost_usd"] = round(_metrics["total_cost_usd"] + qr.est_cost_usd, 6)
        _metrics["total_input_tokens"] += qr.input_tokens
        _metrics["total_output_tokens"] += qr.output_tokens
    return qr.model_dump()


@app.get("/health")
def health():
    return {"status": "ok", "chunks_indexed": len(_pipeline.chunks), "model": getattr(_pipeline.llm, "model", "unknown")}


@app.get("/metrics")
def metrics():
    with _lock:
        lats = _metrics["latencies_ms"]
        return {
            "uptime_s": round(time.time() - _metrics["started_at"], 1),
            "requests": _metrics["requests"],
            "abstention_rate": round(_metrics["abstentions"] / _metrics["requests"], 3) if _metrics["requests"] else 0.0,
            "schema_violations": _metrics["schema_violations"],
            "avg_latency_ms": round(statistics.mean(lats), 2) if lats else 0.0,
            "p95_latency_ms": round(sorted(lats)[int(0.95 * (len(lats) - 1))], 2) if lats else 0.0,
            "total_cost_usd": _metrics["total_cost_usd"],
            "est_cost_per_1000_requests_usd": round(
                _metrics["total_cost_usd"] / _metrics["requests"] * 1000, 4
            ) if _metrics["requests"] else 0.0,
            "total_tokens": {
                "input": _metrics["total_input_tokens"],
                "output": _metrics["total_output_tokens"],
            },
        }


from fastapi.responses import FileResponse


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(ROOT / "static" / "index.html")


from fastapi.responses import PlainTextResponse


@app.get("/prometheus", response_class=PlainTextResponse, include_in_schema=False)
def prometheus_metrics():
    """Prometheus exposition format, hand-rolled from the same counters as
    /metrics - no new dependency. The alert rules in platform/k8s/monitoring
    reference exactly these series names."""
    with _lock:
        req = _metrics["requests"]
        lats = _metrics["latencies_ms"]
        abst_rate = _metrics["abstentions"] / req if req else 0.0
        p95 = sorted(lats)[int(0.95 * (len(lats) - 1))] if lats else 0.0
        lines = [
            "# TYPE rag_requests_total counter",
            f"rag_requests_total {req}",
            "# TYPE rag_abstention_rate gauge",
            f"rag_abstention_rate {abst_rate:.4f}",
            "# TYPE rag_schema_violations_total counter",
            f"rag_schema_violations_total {_metrics['schema_violations']}",
            "# TYPE rag_latency_p95_ms gauge",
            f"rag_latency_p95_ms {p95:.2f}",
            "# TYPE rag_cost_usd_total counter",
            f"rag_cost_usd_total {_metrics['total_cost_usd']:.6f}",
        ]
    return "\n".join(lines) + "\n"
