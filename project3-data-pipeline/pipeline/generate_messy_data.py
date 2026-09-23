"""Deterministic (seeded) generator of genuinely messy raw data.

Three CSV batches simulating ticket exports from three regional office
systems, each with different problems - because the messiness IS the project:

  batch1_lagos.csv     dates DD/MM/YYYY, costs like "N45,000" / "45000 NGN",
                       inconsistent priority labels, some missing subjects
  batch2_accra.csv     SCHEMA DRIFT: different column names entirely;
                       ISO dates mixed with "Mar 3, 2026"; GHS/USD mixed
  batch3_freetown.csv  missing fields, duplicate ticket ids (within batch and
                       colliding with batch 1), an invalid calendar date,
                       negative costs, mojibake from a latin-1 export,
                       status typos, blank lines

Run:  python -m pipeline.generate_messy_data
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

CATEGORIES = ["hardware", "software", "network", "access", "printer", "email"]
SUBJECTS = [
    "Cannot connect to VPN", "Printer offline in finance", "Password reset needed",
    "Laptop screen flickering", "Outlook not syncing", "Shared drive access denied",
    "Wi-Fi drops in conference room", "Sage Evolution login failure",
    "New starter account setup", "UPS beeping in server room",
    "Excel file corrupt", "Projector HDMI no signal", "Phishing email reported",
    "Domain controller replication alert", "Switch port dead on floor 2",
]


def gen_lagos(rng: random.Random) -> list[dict]:
    rows = []
    pri = ["P1", "P2", "P3", "High", "low", "URGENT", "medium", "P4"]
    for i in range(1, 51):
        cost = rng.choice([f"N{rng.randint(5, 900) * 1000:,}",
                           f"{rng.randint(5, 900) * 1000} NGN",
                           str(rng.randint(5, 900) * 1000), ""])
        rows.append({
            "ticket_id": f"LAG-{1000 + i}",
            "opened": f"{rng.randint(1, 28):02d}/{rng.randint(1, 6):02d}/2026",
            "office": rng.choice(["Lagos", "lagos", "LAGOS", "Lagos HQ"]),
            "category": rng.choice(CATEGORIES + ["misc", ""]),
            "priority": rng.choice(pri),
            "cost_ngn": cost,
            "status": rng.choice(["open", "resolved", "in progress", "closed", "OPEN"]),
            "subject": rng.choice(SUBJECTS) if rng.random() > 0.06 else "",
        })
    return rows


def gen_accra(rng: random.Random) -> list[dict]:
    # schema drift: completely different column names
    rows = []
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    for i in range(1, 41):
        if rng.random() < 0.5:
            date = f"2026-{rng.randint(1, 6):02d}-{rng.randint(1, 28):02d}"
        else:
            date = f"{rng.choice(months)} {rng.randint(1, 28)}, 2026"
        cur = rng.choice(["GHS", "GHS", "USD"])
        rows.append({
            "id": f"ACC-{2000 + i}",
            "date_opened": date,
            "location": rng.choice(["Accra", "accra", "Accra Office"]),
            "type": rng.choice(CATEGORIES + ["other"]),
            "urgency": rng.choice(["1", "2", "3", "4", "critical", "normal"]),
            "cost": str(rng.randint(50, 4000)) if rng.random() > 0.08 else "n/a",
            "currency": cur,
            "state": rng.choice(["open", "closed", "resolved", "pending"]),
            "title": rng.choice(SUBJECTS),
        })
    return rows


def gen_freetown(rng: random.Random) -> list[dict]:
    rows = []
    for i in range(1, 31):
        rows.append({
            "ticket_id": f"FNA-{3000 + i}",
            "opened": f"{rng.randint(1, 6):02d}-{rng.randint(1, 28):02d}-2026",  # MM-DD-YYYY
            "office": rng.choice(["Freetown", "freetown", ""]),
            "category": rng.choice(CATEGORIES),
            "priority": rng.choice(["P1", "P2", "P3", "P4"]),
            "cost_ngn": str(rng.randint(10, 500) * 100),
            "status": rng.choice(["open", "resolvd", "closed", "in_progress"]),  # typo!
            "subject": rng.choice(SUBJECTS),
        })
    # inject specific corruption (offices set explicitly so THESE defects,
    # not a coincidental missing office, are what the pipeline must catch)
    for idx in (4, 7, 10, 12, 15, 18, 21):
        rows[idx]["office"] = "Freetown"
        rows[idx]["subject"] = rows[idx]["subject"] or "Follow-up ticket"
    rows[4]["opened"] = "2026-02-30"                    # invalid calendar date
    rows[7]["cost_ngn"] = "-45000"                      # negative cost
    rows[10]["ticket_id"] = "FNA-3005"                  # duplicate within batch
    rows[12]["ticket_id"] = "LAG-1010"                  # collides with batch 1
    rows[15]["ticket_id"] = ""                          # missing id
    rows[18]["subject"] = "Caf\u00e9 kiosk r\u00e9seau down".encode("utf-8").decode("latin-1")  # mojibake
    rows[21]["opened"] = ""                             # missing date
    return rows


def main() -> None:
    rng = random.Random(2026)
    RAW.mkdir(parents=True, exist_ok=True)

    lagos = gen_lagos(rng)
    with (RAW / "batch1_lagos.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(lagos[0].keys()))
        w.writeheader()
        w.writerows(lagos)

    accra = gen_accra(rng)
    with (RAW / "batch2_accra.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(accra[0].keys()))
        w.writeheader()
        w.writerows(accra)

    freetown = gen_freetown(rng)
    with (RAW / "batch3_freetown.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(freetown[0].keys()))
        w.writeheader()
        for i, row in enumerate(freetown):
            w.writerow(row)
            if i in (9, 19):
                f.write("\n")  # stray blank lines
    print(f"wrote {len(lagos)} + {len(accra)} + {len(freetown)} raw rows to {RAW}")


if __name__ == "__main__":
    main()
