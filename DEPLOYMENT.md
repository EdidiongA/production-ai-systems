# Deployment & Operations: All Three Systems

## Infrastructure architecture

```
                    Internet
                       │ TLS
              ┌────────▼─────────┐     auth (API keys), rate limits,
              │ Reverse proxy    │     request-size caps - enforced ONCE
              │ (Caddy/Traefik)  │     here, not per-service
              └──┬─────┬─────┬───┘
        /rag ────┘  /agent  └──── /pipeline
        ┌────────┐ ┌────────┐ ┌──────────┐
        │ rag    │ │ agent  │ │ pipeline │   one container each,
        │ :8000  │ │ :8000  │ │ :8000    │   HEALTHCHECK built into
        └───┬────┘ └───┬────┘ └────┬─────┘   every Dockerfile
            │          │           │
            └──────────┼───────────┘
                       ▼
     volumes: logs/ (JSONL), checkpoints/, data/processed/
     secrets: ANTHROPIC_API_KEY via env/secret store - never in images
```

Sizing honesty: these are single-process services with SQLite/in-memory state.
The right first deployment is **one VM + docker-compose + a proxy**: not
Kubernetes. The K8s section below exists for when horizontal scale is real,
and names the state changes that must happen *first*.

## Deployment workflow

1. Push → CI (below) runs evals + regression gates + docker builds per project.
2. Green main → build tagged images → push to registry.
3. Deploy: `docker compose pull && docker compose up -d` (VM) - rolling by
   service; each container's HEALTHCHECK gates traffic at the proxy.
4. Rollback = redeploy previous tag; state lives in volumes, not images.

## CI/CD pipeline (`.github/workflows/ci.yml`, included)

Matrix over the three projects: install → run the **deterministic mock-backend
eval suite** ($0, no key in CI) → pytest regression gates → `pip-audit`
(non-blocking) → docker build. The property that makes this CI meaningful:
the eval suites are the behavioral contract, so a green pipeline means
"behavior preserved," not just "imports resolve." Optional nightly job with a
scoped `ANTHROPIC_API_KEY` secret runs the same suites against live backends
and publishes the failure-taxonomy diff.

## Docker / Kubernetes

Docker: per-project Dockerfiles (already present: slim base, cached deps
layer, HEALTHCHECK, key via runtime env). `docker-compose.yml` at root runs
all three on :8001–:8003.

Kubernetes readiness - required changes before it makes sense, per service:
- **rag**: stateless apart from logs → ship logs to stdout collector; then
  N replicas trivially. Readiness = `/health`.
- **agent**: move run execution to a queue (Redis + worker deployment);
  `/run` becomes enqueue → 202 + run_id, `/runs/{id}` already exists as the
  poll endpoint. Checkpoints → object storage or PVC.
- **pipeline**: SQLite → Postgres (schema is 2 tables; the Pydantic gate and
  normalizers don't change), ingest as a Job/CronJob, API as a Deployment.
Then: HPA on CPU for rag/pipeline, KEDA on queue depth for agent workers.

## Monitoring & logging strategy

- **Golden signals per service** from existing `/metrics`: rate, errors
  (schema_violations, terminal-status distribution, reject counts), duration
  (avg/p95 latency), saturation (tokens, cost). Production form: replace JSON
  metrics with Prometheus exposition (`prometheus-fastapi-instrumentator`),
  Grafana dashboards, alerting rules below.
- **Domain metrics that matter more than CPU**: P1 abstention_rate (spike =
  retrieval/corpus regression), P2 completion_rate and budget_exceeded rate
  (spike = runaway plans or price change), P3 quality_score per run
  (drop = upstream source drift - this is the pipeline's whole point).
- **Alerts**: p95 latency > 2× baseline; P1 abstention_rate > 0.25;
  P2 completion_rate < 0.8 or est cost/hour > budget; P3 quality_score < 0.75
  (mirrors the pytest gate) or any new reject_reason key.
- **Logs**: JSONL already structured → ship with vector/fluent-bit; logrotate
  with 30-day retention; redaction pass on request text before shipping.

## Reliability / downtime risks

| Risk | Control |
|---|---|
| Model API outage | Mock backend is a real degraded mode: flip env to serve deterministic answers with a `degraded: true` flag rather than 502s |
| Cost runaway (live key) | P2 server-side budget clamp (exists) + per-key spend ceiling at proxy + cost alert |
| Bad deploy | Eval gates in CI + HEALTHCHECK-gated rollout + tag rollback |
| Disk fill (logs/checkpoints) | logrotate + volume alerts at 80% |
| Data corruption on ingest | Conservation asserts abort the run; quality gate blocks silently-degraded loads |

## Production deployment checklist

- [ ] Reverse proxy with TLS, auth keys, rate limits, 1MB request cap
- [ ] `ANTHROPIC_API_KEY` in secret store; confirm absent from `docker history`
- [ ] CI green including eval gates on the exact SHA being deployed
- [ ] Prometheus scrape + Grafana dashboard + the 4 alert rules above
- [ ] Log shipping + rotation + redaction verified with a synthetic secret
- [ ] Volume backups (checkpoints/, processed/) and restore actually rehearsed
- [ ] Load test: sustained 5× expected RPS; verify p95 and cost/hour
- [ ] Runbook: rollback command, degraded-mode flip, on-call contact
