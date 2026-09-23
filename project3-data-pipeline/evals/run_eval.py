"""Pipeline evaluation: three layers.

  A. Data-quality gates - the pipeline's own contract: conservation (nothing
     silently dropped), every injected corruption caught with the right
     reason, quality score computed and persisted.
  B. Naive-vs-pipeline comparison - loads the same raw CSVs with plain pandas
     (what most notebooks do) and shows the concrete damage: currencies
     summed together as one column, negative costs counted, duplicates and
     invalid dates kept. This is the "trace the hallucination back to a
     malformed input record" argument, quantified.
  C. Downstream consumer checks - retrieval returns validated records;
     structured summary passes Pydantic and matches SQL ground truth.

Usage: python -m evals.run_eval
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from consumer.qa import TicketRetriever, get_summary
from pipeline.ingest import run_ingestion

DB = ROOT / "data" / "processed" / "tickets.db"


def check(name: str, ok: bool, detail: str = "") -> dict:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return {"name": name, "passed": bool(ok), "detail": detail}


def main() -> int:
    results = []
    report = run_ingestion()
    print("A. Data-quality gates")
    results.append(check("conservation: accepted+rejected == raw_rows",
                         report["accepted"] + report["rejected"] == report["raw_rows"],
                         f"{report['accepted']}+{report['rejected']}=={report['raw_rows']}"))
    rr = report["reject_reasons"]
    results.append(check("invalid calendar date (2026-02-30) caught",
                         rr.get("schema_opened_date_value_error", 0) >= 1))
    results.append(check("negative cost rejected by schema",
                         rr.get("schema_cost_usd_greater_than_equal", 0) >= 1))
    results.append(check("duplicate ticket id rejected",
                         rr.get("duplicate_ticket_id", 0) >= 1))
    results.append(check("missing ticket id rejected",
                         rr.get("missing_ticket_id", 0) >= 1))
    results.append(check("quality score in (0,1) and persisted",
                         0 < report["quality_score"] < 1,
                         str(report["quality_score"])))
    rejected_lines = [json.loads(line) for line in
                      (ROOT / "logs" / "rejected.jsonl").read_text().splitlines()]
    this_run = [r for r in rejected_lines if r["run_id"] == report["run_id"]]
    results.append(check("every rejection logged with reason + raw record",
                         len(this_run) == report["rejected"] and
                         all("reason" in r and "raw" in r for r in this_run)))
    # mojibake repair check
    conn = sqlite3.connect(DB)
    moji = conn.execute("SELECT subject FROM tickets WHERE subject LIKE '%Café%'").fetchall()
    results.append(check("mojibake subject repaired to UTF-8",
                         len(moji) >= 1, moji[0][0] if moji else "not found"))

    print("B. Naive load vs pipeline (the damage a validation layer prevents)")
    naive_frames = []
    for name in ("batch1_lagos.csv", "batch3_freetown.csv"):
        df = pd.read_csv(ROOT / "data" / "raw" / name)
        df["cost_raw"] = df["cost_ngn"]
        naive_frames.append(df[["ticket_id", "cost_raw"]])
    df2 = pd.read_csv(ROOT / "data" / "raw" / "batch2_accra.csv")
    df2 = df2.rename(columns={"id": "ticket_id", "cost": "cost_raw"})
    naive_frames.append(df2[["ticket_id", "cost_raw"]])
    naive = pd.concat(naive_frames, ignore_index=True)

    def naive_num(x):
        try:
            return float(re.sub(r"[^\d.-]", "", str(x)) or "nan")
        except ValueError:
            return float("nan")

    naive_total = naive["cost_raw"].map(naive_num).sum()  # NGN+GHS+USD mixed!
    pipe_total = conn.execute("SELECT ROUND(SUM(cost_usd),2) FROM tickets").fetchone()[0]
    naive_dupes = int(naive["ticket_id"].duplicated().sum())
    ratio = naive_total / pipe_total if pipe_total else float("inf")
    results.append(check("naive total is a meaningless cross-currency number",
                         ratio > 100,
                         f"naive={naive_total:,.0f} (mixed units) vs pipeline={pipe_total:,.2f} USD"))
    results.append(check("naive load keeps duplicate ids the pipeline rejects",
                         naive_dupes >= 1, f"{naive_dupes} duplicate id(s) kept"))
    results.append(check("naive load keeps the negative cost",
                         (naive["cost_raw"].map(naive_num) < 0).any()))

    print("C. Downstream consumer")
    retriever = TicketRetriever()
    hits = retriever.search("printer offline finance")
    results.append(check("retrieval returns printer tickets from validated rows",
                         bool(hits) and any("printer" in (h["subject"] + h["category"]).lower()
                                            for h in hits),
                         f"top: {hits[0]['ticket_id']} {hits[0]['subject'][:40]}" if hits else ""))
    hits2 = retriever.search("VPN connection issue Accra")
    results.append(check("retrieval handles multi-field queries",
                         bool(hits2)))
    summary = get_summary()  # raises if schema-invalid
    sql_total = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    sql_cost = conn.execute("SELECT ROUND(SUM(cost_usd),2) FROM tickets").fetchone()[0]
    sql_p1 = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE status='open' AND priority='P1'").fetchone()[0]
    results.append(check("structured summary validates against TicketSummary schema", True))
    results.append(check("summary numbers match SQL ground truth",
                         summary.total_tickets == sql_total and
                         abs(summary.total_cost_usd - sql_cost) < 0.01 and
                         summary.open_p1_count == sql_p1,
                         f"{summary.total_tickets} tickets, {summary.total_cost_usd} USD, "
                         f"{summary.open_p1_count} open P1"))
    conn.close()

    passed = sum(r["passed"] for r in results)
    summary_out = {"checks": len(results), "passed": passed,
                   "pass_rate": round(passed / len(results), 3),
                   "ingest_report": report}
    (ROOT / "evals" / "report.json").write_text(
        json.dumps({"summary": summary_out, "results": results}, indent=2))
    print(f"\n{passed}/{len(results)} checks passed | quality_score={report['quality_score']}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
