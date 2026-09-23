"""Tool registry: 4 callable tools with typed (Pydantic) inputs and outputs.

Every tool returns a ToolResult, never raises into the loop. Failure modes are
first-class: SQL errors, missing files, and a simulated flaky FX API (timeout
on first call for certain currencies) are all realistic behaviors the agent
must survive, and the eval suite includes tasks that trigger them.
"""
from __future__ import annotations

import math
import re
import sqlite3
import time
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

ROOT = Path(__file__).resolve().parents[1]


class ToolResult(BaseModel):
    tool: str
    ok: bool
    output: str          # stringified result the model reads
    error: str | None = None
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------- calculator
class CalculatorArgs(BaseModel):
    expression: str = Field(max_length=200, description="arithmetic expression")

_ALLOWED = re.compile(r"^[0-9+\-*/(). %]+$")

def calculator(args: dict) -> ToolResult:
    t0 = time.perf_counter()
    try:
        a = CalculatorArgs.model_validate(args)
        if not _ALLOWED.match(a.expression):
            raise ValueError("expression contains disallowed characters")
        if "**" in a.expression:
            raise ValueError("exponentiation is not supported")
        if any(len(n) > 12 for n in re.findall(r"\d+", a.expression)):
            raise ValueError("operand too large")
        value = eval(a.expression, {"__builtins__": {}}, {})
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise ValueError("non-finite result")
        return ToolResult(tool="calculator", ok=True, output=str(round(value, 4)),
                          elapsed_ms=(time.perf_counter() - t0) * 1000)
    except (ValidationError, Exception) as exc:  # noqa: BLE001
        return ToolResult(tool="calculator", ok=False, output="", error=str(exc)[:200],
                          elapsed_ms=(time.perf_counter() - t0) * 1000)


# ------------------------------------------------------------------ db_query
class DbQueryArgs(BaseModel):
    sql: str = Field(max_length=500, description="single SELECT statement")

def db_query(args: dict) -> ToolResult:
    t0 = time.perf_counter()
    try:
        a = DbQueryArgs.model_validate(args)
        sql = a.sql.strip().rstrip(";")
        if not sql.lower().startswith("select"):
            raise ValueError("only SELECT statements are permitted")
        conn = sqlite3.connect(ROOT / "data" / "assets.db")
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql)
        rows = [dict(r) for r in cur.fetchmany(50)]
        conn.close()
        return ToolResult(tool="db_query", ok=True, output=repr(rows),
                          elapsed_ms=(time.perf_counter() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(tool="db_query", ok=False, output="", error=str(exc)[:200],
                          elapsed_ms=(time.perf_counter() - t0) * 1000)


# ----------------------------------------------------------------- kb_search
class KbSearchArgs(BaseModel):
    query: str = Field(max_length=200)

def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))

def kb_search(args: dict) -> ToolResult:
    """Lightweight lexical retrieval over the policy knowledge base."""
    t0 = time.perf_counter()
    try:
        a = KbSearchArgs.model_validate(args)
        q = _tokenize(a.query)
        best: tuple[float, str, str] | None = None
        for path in sorted((ROOT / "data" / "kb").glob("*.md")):
            for para in re.split(r"\n\s*\n", path.read_text()):
                para = para.strip()
                if not para or para.startswith("#"):
                    continue
                overlap = len(q & _tokenize(para)) / max(len(q), 1)
                if best is None or overlap > best[0]:
                    best = (overlap, path.stem, para)
        if best is None or best[0] < 0.15:
            return ToolResult(tool="kb_search", ok=False, output="",
                              error="no policy paragraph matched the query",
                              elapsed_ms=(time.perf_counter() - t0) * 1000)
        return ToolResult(tool="kb_search", ok=True,
                          output=f"[{best[1]}] {best[2]}",
                          elapsed_ms=(time.perf_counter() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(tool="kb_search", ok=False, output="", error=str(exc)[:200],
                          elapsed_ms=(time.perf_counter() - t0) * 1000)


# ----------------------------------------------------------------- fx_lookup
class FxLookupArgs(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO code, e.g. NGN")

_FX_RATES = {"NGN": 1495.0, "GHS": 14.8, "USD": 1.0, "EUR": 0.91}
_fx_call_count: dict[str, int] = {}

def fx_lookup(args: dict) -> ToolResult:
    """Mocked FX API with a realistic failure mode: NGN times out on the
    first call of every process (simulating a cold/flaky upstream), succeeds
    on retry. Unknown currencies return a 404-style error."""
    t0 = time.perf_counter()
    try:
        a = FxLookupArgs.model_validate(args)
        _fx_call_count[a.currency] = _fx_call_count.get(a.currency, 0) + 1
        if a.currency == "NGN" and _fx_call_count[a.currency] == 1:
            time.sleep(0.05)
            return ToolResult(tool="fx_lookup", ok=False, output="",
                              error="upstream timeout after 5000ms (simulated)",
                              elapsed_ms=(time.perf_counter() - t0) * 1000)
        if a.currency not in _FX_RATES:
            return ToolResult(tool="fx_lookup", ok=False, output="",
                              error=f"currency {a.currency} not found (404)",
                              elapsed_ms=(time.perf_counter() - t0) * 1000)
        return ToolResult(tool="fx_lookup", ok=True,
                          output=f"1 USD = {_FX_RATES[a.currency]} {a.currency}",
                          elapsed_ms=(time.perf_counter() - t0) * 1000)
    except ValidationError as exc:
        return ToolResult(tool="fx_lookup", ok=False, output="", error=str(exc)[:200],
                          elapsed_ms=(time.perf_counter() - t0) * 1000)


# ----------------------------------------------------------------- file_read
class FileReadArgs(BaseModel):
    filename: str = Field(max_length=100, description="file under data/files/")

def file_read(args: dict) -> ToolResult:
    t0 = time.perf_counter()
    try:
        a = FileReadArgs.model_validate(args)
        if "/" in a.filename or ".." in a.filename:
            raise ValueError("path traversal rejected")
        path = ROOT / "data" / "files" / a.filename
        if not path.exists():
            return ToolResult(tool="file_read", ok=False, output="",
                              error=f"file not found: {a.filename}",
                              elapsed_ms=(time.perf_counter() - t0) * 1000)
        return ToolResult(tool="file_read", ok=True, output=path.read_text()[:2000],
                          elapsed_ms=(time.perf_counter() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(tool="file_read", ok=False, output="", error=str(exc)[:200],
                          elapsed_ms=(time.perf_counter() - t0) * 1000)


TOOLS = {
    "calculator": (calculator, CalculatorArgs),
    "db_query": (db_query, DbQueryArgs),
    "kb_search": (kb_search, KbSearchArgs),
    "fx_lookup": (fx_lookup, FxLookupArgs),
    "file_read": (file_read, FileReadArgs),
}

TOOL_DESCRIPTIONS = """
Available tools:
- db_query(sql): run one SELECT against the assets table
  (columns: id, hostname, asset_type, location, os, status, warranty_end, renewal_cost_usd, role)
- calculator(expression): arithmetic only
- kb_search(query): search IT policy knowledge base, returns best paragraph
- fx_lookup(currency): USD exchange rate for an ISO currency code
- file_read(filename): read a file under data/files/
"""
