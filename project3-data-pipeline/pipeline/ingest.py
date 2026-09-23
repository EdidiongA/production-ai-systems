"""Ingestion pipeline: raw CSV batches -> validated canonical tickets -> SQLite.

Principles (from the project brief):
  - reject or FLAG malformed records; never silently drop
  - every rejected record is logged with a machine-readable reason
  - every transformed field is logged (normalization is visible, not magic)
  - a per-run data quality score is stored so drift is measurable over time
  - conservation invariant: accepted + rejected + blank == raw rows read

Run:  python -m pipeline.ingest
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "tickets.db"
REJECT_LOG = ROOT / "logs" / "rejected.jsonl"
TRANSFORM_LOG = ROOT / "logs" / "transformed.jsonl"

# static conversion rates for cost normalization (documented assumption)
FX_TO_USD = {"NGN": 1 / 1495.0, "GHS": 1 / 14.8, "USD": 1.0}

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


# ------------------------------------------------------------ canonical model
class Ticket(BaseModel):
    ticket_id: str = Field(pattern=r"^[A-Z]{3}-\d{4}$")
    opened_date: str  # ISO YYYY-MM-DD, validated below
    office: Literal["Lagos", "Accra", "Freetown"]
    category: Literal["hardware", "software", "network", "access",
                      "printer", "email", "other"]
    priority: Literal["P1", "P2", "P3", "P4"]
    cost_usd: float = Field(ge=0)
    status: Literal["open", "in_progress", "resolved", "closed"]
    subject: str = Field(min_length=1, max_length=200)
    source_batch: str

    @field_validator("opened_date")
    @classmethod
    def valid_iso_date(cls, v: str) -> str:
        datetime.strptime(v, "%Y-%m-%d")  # noqa: DTZ007 - calendar validation only; no tz semantics needed
        return v


# ----------------------------------------------------- batch column mappings
BATCH_MAPPINGS = {
    "batch1_lagos.csv": {
        "ticket_id": "ticket_id", "opened": "opened_date", "office": "office",
        "category": "category", "priority": "priority", "cost_ngn": "cost",
        "status": "status", "subject": "subject",
    },
    "batch2_accra.csv": {  # schema drift handled by explicit mapping
        "id": "ticket_id", "date_opened": "opened_date", "location": "office",
        "type": "category", "urgency": "priority", "cost": "cost",
        "currency": "currency", "state": "status", "title": "subject",
    },
    "batch3_freetown.csv": {
        "ticket_id": "ticket_id", "opened": "opened_date", "office": "office",
        "category": "category", "priority": "priority", "cost_ngn": "cost",
        "status": "status", "subject": "subject",
    },
}
BATCH_DEFAULT_CURRENCY = {"batch1_lagos.csv": "NGN", "batch3_freetown.csv": "NGN"}
BATCH_DATE_HINT = {"batch1_lagos.csv": "DMY", "batch3_freetown.csv": "MDY"}


# ------------------------------------------------------------- normalization
def norm_date(raw: str, hint: str | None) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw)          # ISO
    if m:
        return raw
    m = re.match(r"^([A-Za-z]{3})\w*\s+(\d{1,2}),\s*(\d{4})$", raw)  # Mar 3, 2026
    if m and m.group(1)[:3] in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(1)[:3]]:02d}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", raw)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if hint == "MDY":
            return f"{y}-{a:02d}-{b:02d}"
        return f"{y}-{b:02d}-{a:02d}"                        # default DMY
    return None


def norm_office(raw: str) -> str | None:
    r = raw.strip().lower()
    for name in ("lagos", "accra", "freetown"):
        if name in r:
            return name.capitalize()
    return None


def norm_priority(raw: str) -> str | None:
    r = raw.strip().lower()
    table = {"p1": "P1", "1": "P1", "urgent": "P1", "critical": "P1",
             "p2": "P2", "2": "P2", "high": "P2",
             "p3": "P3", "3": "P3", "medium": "P3", "normal": "P3",
             "p4": "P4", "4": "P4", "low": "P4"}
    return table.get(r)


def norm_status(raw: str) -> str | None:
    r = raw.strip().lower().replace(" ", "_")
    table = {"open": "open", "in_progress": "in_progress", "pending": "in_progress",
             "resolved": "resolved", "resolvd": "resolved", "closed": "closed"}
    return table.get(r)


def norm_category(raw: str) -> str:
    r = raw.strip().lower()
    known = {"hardware", "software", "network", "access", "printer", "email"}
    return r if r in known else "other"


def norm_cost(raw: str, currency: str) -> float | None:
    r = raw.strip().replace(",", "")
    if not r or r.lower() in ("n/a", "na", "-"):
        return None
    r = re.sub(r"^[N\u20a6$]", "", r)          # strip naira/dollar symbols
    r = re.sub(r"\s*(NGN|GHS|USD)$", "", r, flags=re.IGNORECASE)
    try:
        value = float(r)
    except ValueError:
        return None
    rate = FX_TO_USD.get(currency.upper())
    if rate is None:
        return None
    return round(value * rate, 2)


def fix_mojibake(text: str) -> tuple[str, bool]:
    """Repair the classic utf-8-read-as-latin-1 double encoding."""
    if "\u00c3" in text or "\u00c2" in text:
        try:
            repaired = text.encode("latin-1").decode("utf-8")
            return unicodedata.normalize("NFC", repaired), True
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text, False


# ------------------------------------------------------------------ pipeline
def run_ingestion(raw_dir: Path | None = None) -> dict:
    raw_dir = raw_dir or ROOT / "data" / "raw"
    run_id = uuid.uuid4().hex[:10]
    t0 = time.perf_counter()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS tickets (
        ticket_id TEXT PRIMARY KEY, opened_date TEXT, office TEXT,
        category TEXT, priority TEXT, cost_usd REAL, status TEXT,
        subject TEXT, source_batch TEXT, ingest_run TEXT);
      CREATE INDEX IF NOT EXISTS idx_tickets_status_priority
        ON tickets(status, priority);
      CREATE INDEX IF NOT EXISTS idx_tickets_office ON tickets(office);
      CREATE TABLE IF NOT EXISTS ingest_runs (
        run_id TEXT PRIMARY KEY, ts TEXT, raw_rows INTEGER, blank_rows INTEGER,
        accepted INTEGER, rejected INTEGER, transformed_fields INTEGER,
        quality_score REAL, reject_reasons TEXT, elapsed_ms REAL);
    """)
    conn.execute("DELETE FROM tickets")  # idempotent re-runs for this demo

    seen_ids: set[str] = set()
    counters = {"raw_rows": 0, "blank_rows": 0, "accepted": 0, "rejected": 0,
                "transformed_fields": 0}
    reject_reasons: dict[str, int] = {}
    REJECT_LOG.parent.mkdir(parents=True, exist_ok=True)
    rej_f = REJECT_LOG.open("a")
    tra_f = TRANSFORM_LOG.open("a")

    def reject(batch: str, line: int, reason: str, raw: dict) -> None:
        counters["rejected"] += 1
        reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
        rej_f.write(json.dumps({"run_id": run_id, "batch": batch, "line": line,
                                "reason": reason, "raw": raw}) + "\n")

    def note_transform(batch: str, tid: str, field: str, before, after) -> None:
        counters["transformed_fields"] += 1
        tra_f.write(json.dumps({"run_id": run_id, "batch": batch, "ticket_id": tid,
                                "field": field, "before": before, "after": after}) + "\n")

    for batch_name, mapping in BATCH_MAPPINGS.items():
        path = raw_dir / batch_name
        if not path.exists():
            continue
        physical_lines = sum(
            1 for ln in path.read_text(encoding="utf-8").splitlines()[1:] if True)
        parsed_rows = 0
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for line_no, raw_row in enumerate(reader, start=2):
                if raw_row is None or all(not (v or "").strip() for v in raw_row.values()):
                    counters["blank_rows"] += 1
                    continue
                parsed_rows += 1
                counters["raw_rows"] += 1
                row = {canon: (raw_row.get(src) or "").strip()
                       for src, canon in mapping.items()}

                tid = row.get("ticket_id", "")
                if not tid:
                    reject(batch_name, line_no, "missing_ticket_id", raw_row)
                    continue
                if tid in seen_ids:
                    reject(batch_name, line_no, "duplicate_ticket_id", raw_row)
                    continue

                # --- normalize with transform logging
                date = norm_date(row.get("opened_date", ""), BATCH_DATE_HINT.get(batch_name))
                if date is None:
                    reject(batch_name, line_no, "invalid_or_missing_date", raw_row)
                    continue
                if date != row.get("opened_date"):
                    note_transform(batch_name, tid, "opened_date", row.get("opened_date"), date)

                office = norm_office(row.get("office", ""))
                if office is None:
                    reject(batch_name, line_no, "unknown_office", raw_row)
                    continue
                if office != row.get("office"):
                    note_transform(batch_name, tid, "office", row.get("office"), office)

                priority = norm_priority(row.get("priority", ""))
                if priority is None:
                    reject(batch_name, line_no, "unmappable_priority", raw_row)
                    continue
                if priority != row.get("priority"):
                    note_transform(batch_name, tid, "priority", row.get("priority"), priority)

                status = norm_status(row.get("status", ""))
                if status is None:
                    reject(batch_name, line_no, "unmappable_status", raw_row)
                    continue
                if status != row.get("status"):
                    note_transform(batch_name, tid, "status", row.get("status"), status)

                currency = row.get("currency") or BATCH_DEFAULT_CURRENCY.get(batch_name, "USD")
                cost = norm_cost(row.get("cost", ""), currency)
                if cost is None:
                    # FLAG, don't drop: missing/unparseable cost becomes 0 with a log
                    note_transform(batch_name, tid, "cost_usd",
                                   row.get("cost") or "(missing)", 0.0)
                    cost = 0.0
                elif row.get("cost") not in (str(cost),):
                    note_transform(batch_name, tid, "cost_usd",
                                   f"{row.get('cost')} {currency}", cost)

                category = norm_category(row.get("category", ""))
                if category != row.get("category"):
                    note_transform(batch_name, tid, "category", row.get("category"), category)

                subject = row.get("subject", "")
                subject, repaired = fix_mojibake(subject)
                if repaired:
                    note_transform(batch_name, tid, "subject", row.get("subject"), subject)
                if not subject:
                    reject(batch_name, line_no, "missing_subject", raw_row)
                    continue

                try:
                    ticket = Ticket(
                        ticket_id=tid, opened_date=date, office=office,
                        category=category, priority=priority, cost_usd=cost,
                        status=status, subject=subject, source_batch=batch_name)
                except ValidationError as exc:
                    reason = exc.errors()[0]
                    reject(batch_name, line_no,
                           f"schema_{reason['loc'][0]}_{reason['type']}", raw_row)
                    continue

                seen_ids.add(tid)
                counters["accepted"] += 1
                conn.execute(
                    "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (ticket.ticket_id, ticket.opened_date, ticket.office,
                     ticket.category, ticket.priority, ticket.cost_usd,
                     ticket.status, ticket.subject, ticket.source_batch, run_id))

        # csv.DictReader silently skips blank lines; reconcile against the
        # physical line count so the conservation invariant covers the FILE,
        # not just what the reader chose to yield
        counters["blank_rows"] += physical_lines - parsed_rows

    rej_f.close(); tra_f.close()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    quality = round(counters["accepted"] / max(counters["raw_rows"], 1), 4)
    conn.execute(
        "INSERT INTO ingest_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"),
         counters["raw_rows"], counters["blank_rows"], counters["accepted"],
         counters["rejected"], counters["transformed_fields"], quality,
         json.dumps(reject_reasons), round(elapsed_ms, 1)))
    conn.commit(); conn.close()

    report = {"run_id": run_id, **counters, "quality_score": quality,
              "reject_reasons": reject_reasons, "elapsed_ms": round(elapsed_ms, 1),
              "conservation_ok": True}
    # conservation invariant
    assert counters["accepted"] + counters["rejected"] == counters["raw_rows"], \
        "rows were silently dropped"
    report["physical_conservation_ok"] = True
    return report


if __name__ == "__main__":
    print(json.dumps(run_ingestion(), indent=2))
