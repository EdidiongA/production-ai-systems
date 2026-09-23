"""Downstream consumer of the pipeline: the reason the data had to be clean.

Two capabilities:
  1. Retrieval Q&A - lexical (BM25-lite) retrieval over ticket subjects,
     answering "what tickets mention X" style questions from validated rows.
  2. Structured executive summary - an LLM (Anthropic API when
     ANTHROPIC_API_KEY is set, deterministic mock otherwise) turns the
     aggregate context into a JSON TicketSummary; output is Pydantic-validated
     either way, so downstream code never parses prose.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "tickets.db"


# ------------------------------------------------------------------ retrieval
def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class TicketRetriever:
    """BM25 over ticket subject + category + office."""

    def __init__(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        self.rows = [dict(r) for r in conn.execute(
            "SELECT ticket_id, opened_date, office, category, priority, "
            "cost_usd, status, subject FROM tickets")]
        conn.close()
        self._docs = [_tokenize(f"{r['subject']} {r['category']} {r['office']} {r['priority']} {r['status']}")
                      for r in self.rows]
        self._lens = [len(d) for d in self._docs]
        self._avg = sum(self._lens) / max(len(self._lens), 1)
        self._tfs = [Counter(d) for d in self._docs]
        df: Counter = Counter()
        for d in self._docs:
            df.update(set(d))
        n = len(self.rows)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        q = _tokenize(query)
        k1, b = 1.5, 0.75
        scores = []
        for i in range(len(self.rows)):
            s = 0.0
            for t in q:
                if t in self._tfs[i]:
                    tf = self._tfs[i][t]
                    s += self._idf.get(t, 0) * tf * (k1 + 1) / (
                        tf + k1 * (1 - b + b * self._lens[i] / self._avg))
            scores.append(s)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [{**self.rows[i], "score": round(scores[i], 3)}
                for i in order[:top_k] if scores[i] > 0]


# ------------------------------------------------------- structured summary
class OfficeStat(BaseModel):
    office: str
    tickets: int
    open_tickets: int
    cost_usd: float


class TicketSummary(BaseModel):
    total_tickets: int = Field(ge=0)
    total_cost_usd: float = Field(ge=0)
    open_p1_count: int = Field(ge=0)
    by_office: list[OfficeStat]
    top_categories: list[str] = Field(max_length=3)
    narrative: str = Field(min_length=20, max_length=600)
    data_quality_note: str


def _aggregates() -> dict:
    conn = sqlite3.connect(DB_PATH)
    agg = {
        "total": conn.execute("SELECT COUNT(*), ROUND(SUM(cost_usd),2) FROM tickets").fetchone(),
        "open_p1": conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE status='open' AND priority='P1'").fetchone()[0],
        "by_office": conn.execute(
            "SELECT office, COUNT(*), SUM(CASE WHEN status='open' THEN 1 ELSE 0 END), "
            "ROUND(SUM(cost_usd),2) FROM tickets GROUP BY office ORDER BY 2 DESC").fetchall(),
        "top_cats": [r[0] for r in conn.execute(
            "SELECT category, COUNT(*) c FROM tickets GROUP BY category ORDER BY c DESC LIMIT 3")],
        "last_run": conn.execute(
            "SELECT quality_score, rejected, reject_reasons FROM ingest_runs "
            "ORDER BY ts DESC LIMIT 1").fetchone(),
    }
    conn.close()
    return agg


class MockSummarizer:
    model = "mock-summarizer-v1"

    def summarize(self, agg: dict) -> str:
        total, cost = agg["total"]
        busiest = agg["by_office"][0]
        quality, rejected, reasons = agg["last_run"]
        return json.dumps({
            "total_tickets": total,
            "total_cost_usd": cost,
            "open_p1_count": agg["open_p1"],
            "by_office": [
                {"office": o, "tickets": n, "open_tickets": op, "cost_usd": c}
                for o, n, op, c in agg["by_office"]],
            "top_categories": agg["top_cats"],
            "narrative": (
                f"{total} validated tickets totalling {cost} USD across three offices. "
                f"{busiest[0]} carries the largest volume ({busiest[1]} tickets, "
                f"{busiest[2]} open). {agg['open_p1']} P1 tickets are currently open "
                f"and should be reviewed first."),
            "data_quality_note": (
                f"Last ingest accepted {round(quality * 100, 1)}% of raw rows; "
                f"{rejected} records rejected ({reasons})."),
        })


class AnthropicSummarizer:
    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self.model = model
        self.api_key = os.environ["ANTHROPIC_API_KEY"]

    def summarize(self, agg: dict) -> str:
        import urllib.request
        prompt = (
            "Produce a JSON TicketSummary with keys total_tickets, total_cost_usd, "
            "open_p1_count, by_office (office/tickets/open_tickets/cost_usd), "
            "top_categories (max 3), narrative, data_quality_note. Use ONLY these "
            f"aggregates, invent nothing: {json.dumps(agg, default=str)}. "
            "Raw JSON only.")
        body = json.dumps({"model": self.model, "max_tokens": 800,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return "".join(b.get("text", "") for b in data.get("content", []))


def get_summary() -> TicketSummary:
    agg = _aggregates()
    backend = (AnthropicSummarizer() if os.environ.get("ANTHROPIC_API_KEY")
               else MockSummarizer())
    raw = backend.summarize(agg)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return TicketSummary.model_validate(json.loads(raw))


if __name__ == "__main__":
    print(get_summary().model_dump_json(indent=2))
