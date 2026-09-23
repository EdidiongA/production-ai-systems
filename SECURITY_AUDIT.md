# Security Audit: All Three Systems

Scope: the three FastAPI services, their tool/ingestion surfaces, and data
handling. Severity scale: CRITICAL / HIGH / MEDIUM / LOW / INFO. Status:
✅ FIXED (applied and verified), ◑ MITIGATED (partial control in place),
→ RECOMMEND (production requirement, out of local-demo scope, with the
concrete implementation named).

## Threat model

Local/demo deployment today; the audit assumes the realistic next step -
exposed on a network, live `ANTHROPIC_API_KEY` set. Assets at risk: the API
key, compute (DoS), the integrity of stored data, and any sensitive content
that could enter logs.

---

## Findings

### F1 · HIGH · Agent calculator: algorithmic-complexity DoS · ✅ FIXED
**System:** P2 `tools/registry.py`. **Vector:** charset whitelist permitted
`*`, so `9**9**9` validated and hangs the worker in big-int arithmetic - one
request, full CPU, no crash, no log. **Attack scenario:** unauthenticated
client posts a goal that induces the expression (or hits a future endpoint
exposing the tool); service degrades for all tenants. **Fix applied:** reject
`**` and operands >12 digits inside the tool's never-raise contract; verified
returns `ToolResult(ok=False)` and the eval suite still passes 10/10.
**Class:** validators that whitelist characters must also bound composition.

### F2 · HIGH · No authentication on any endpoint · → RECOMMEND
All endpoints are anonymous. Ranked by consequence: P2 `/run` **spends money**
per request when a live key is set (financial DoS = budget_ceiling ×
requests); P3 `/ingest` mutates the datastore; P1 `/ask` spends per query.
**Recommendation (concrete):** API-key middleware (FastAPI dependency checking
`Authorization: Bearer` against hashed keys), per-key rate limits and spend
ceilings; put the services behind a reverse proxy (Caddy/Traefik) for TLS.
Not stubbed with a hardcoded key on purpose - fake auth is worse than
documented no-auth.

### F3 · MEDIUM · SQL injection surface: defense in depth holds · ◑ MITIGATED
P2 `db_query` accepts model-authored SQL. Controls in place: SELECT-only
enforcement, read-only data, parameter-free single statement, never-raise
wrapper. Residual risk: `SELECT` can still exfiltrate anything in the DB
(acceptable: the DB is the agent's intended read surface) and sqlite
extensions are absent (no `load_extension`). **Hardening for production:**
open the connection with `mode=ro` URI and `PRAGMA query_only=ON`, so the
guarantee is enforced by the engine, not by string inspection.

### F4 · MEDIUM · Prompt injection via retrieved/ingested content · ◑ MITIGATED
**P1:** corpus is trusted-static today, but the pipeline pattern will be reused
with user documents; a document containing "cite chunk X and answer Y" is the
attack. Existing control that genuinely helps: the citation guardrail bounds
*attribution* (can't cite unretrieved chunks) and schema validation bounds
*shape*; neither bounds *content*. **P2:** `kb_search` output enters the
planner prompt - same class. **Recommendation:** treat retrieved text as data
(delimited, with an explicit instruction hierarchy in the live prompts), and
add eval canaries containing injection strings, the eval harness is the right
place to make this measurable.

### F5 · MEDIUM · API key handling · ◑ MITIGATED
Key is read from the environment only, never logged, never echoed in
responses; Dockerfiles take it as a runtime `-e`, not a build ARG (verified -
no key can be baked into an image layer). Residual: JSONL request logs store
full queries and answers; if users paste secrets into questions, they persist.
**Recommendation:** log-field redaction pass + logrotate with retention (see
DEPLOYMENT.md).

### F6 · LOW · Path traversal in file_read · ✅ pre-existing control verified
P2 `file_read` resolves paths and rejects escapes from `data/files/`.
Verified: `../../etc/passwd` → `ToolResult(ok=False)`. Kept under regression
by the tool's typed-args test path.

### F7 · LOW · Rate limiting only on P1 · → RECOMMEND
P1 has 60/min in-process. P2/P3 rely on... nothing. In-process limiters also
don't survive multi-worker deployment (per-process counters). **Recommendation:**
enforce at the reverse proxy (single place, all services) rather than
duplicating in-app limiters, which is why parity was *not* hacked in here.

### F8 · LOW · CSV/formula injection on future export paths · INFO
P3 stores subjects verbatim; if a future feature exports CSVs opened in Excel,
cells beginning `=`, `+`, `-`, `@` execute as formulas. No export exists today.
Guard to add with the feature: prefix-quote dangerous leading characters.

### F9 · INFO · UI output encoding · ✅ built-in
All three consoles HTML-escape every dynamic value before DOM insertion
(single `esc()` helper, no `innerHTML` of raw API text), so a malicious ticket
subject or model answer cannot XSS the dashboard. No cookies, no localStorage,
no third-party scripts, the UI's attack surface is intentionally near-zero.

### F10 · INFO · Dependency posture
Five direct dependencies per service, all mainstream (fastapi, uvicorn,
pydantic, pandas, pytest). Recommendation: `pip-audit` step in CI (included
in the CI workflow, non-blocking).

## Priority order for production hardening
1. F2 auth + proxy TLS (gates everything else)
2. F7 proxy rate limits + per-key spend ceilings on P2
3. F3 engine-level read-only (`query_only=ON`)
4. F4 injection canaries in the eval sets
5. F5 log redaction + retention
