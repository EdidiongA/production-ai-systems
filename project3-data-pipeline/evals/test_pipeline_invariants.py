"""Pipeline invariants. Run:  pytest evals/test_pipeline_invariants.py -q"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.ingest import (
    DB_PATH,
    norm_cost,
    norm_date,
    norm_priority,
    run_ingestion,
)


@pytest.fixture(scope="module")
def report():
    return run_ingestion()


def test_conservation(report):
    assert report["accepted"] + report["rejected"] == report["raw_rows"]


def test_quality_floor(report):
    assert report["quality_score"] >= 0.75, "ingest quality regressed below 75%"


def test_no_invalid_rows_in_db(report):
    conn = sqlite3.connect(DB_PATH)
    assert conn.execute("SELECT COUNT(*) FROM tickets WHERE cost_usd < 0").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE office NOT IN ('Lagos','Accra','Freetown')"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM (SELECT ticket_id FROM tickets GROUP BY ticket_id HAVING COUNT(*)>1)"
    ).fetchone()[0] == 0
    conn.close()


def test_every_rejection_has_reason(report):
    lines = [json.loads(x) for x in (ROOT / "logs" / "rejected.jsonl").read_text().splitlines()]
    ours = [x for x in lines if x["run_id"] == report["run_id"]]
    assert len(ours) == report["rejected"]
    assert all(x["reason"] for x in ours)


def test_date_normalizer():
    assert norm_date("2026-03-05", None) == "2026-03-05"
    assert norm_date("05/03/2026", "DMY") == "2026-03-05"
    assert norm_date("03-05-2026", "MDY") == "2026-03-05"
    assert norm_date("Mar 5, 2026", None) == "2026-03-05"
    assert norm_date("garbage", None) is None


def test_cost_normalizer():
    assert norm_cost("N45,000", "NGN") == round(45000 / 1495.0, 2)
    assert norm_cost("45000 NGN", "NGN") == round(45000 / 1495.0, 2)
    assert norm_cost("100", "USD") == 100.0
    assert norm_cost("n/a", "USD") is None


def test_priority_normalizer():
    assert norm_priority("URGENT") == "P1"
    assert norm_priority("high") == "P2"
    assert norm_priority("3") == "P3"
    assert norm_priority("weird") is None
