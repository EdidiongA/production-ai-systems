# Strategic Direction: From Three Portfolio Systems to a Platform and an Offer

## 1. The reframe (the architect's correction)

The question was "can one of these systems deploy a Kubernetes cluster?" -
and the honest architectural answer is that it shouldn't. Application systems
that provision their own infrastructure couple two lifecycles that must stay
separate. The right shape is three layers:

```
LAYER 3  DELIVERY ASSETS      case studies, runbooks, audits, this strategy
         (what clients buy)   doc, the packaging of layers 1-2 as proof
LAYER 2  PLATFORM             Terraform -> AWS (VPC, EKS, ECR, IRSA)
         (heavy lifting)      K8s manifests, monitoring, OIDC deploy pipeline
                              -> platform/ (built, validated)
LAYER 1  PRODUCT SYSTEMS      RAG API · Agent API · Data Pipeline
         (the workloads)      -> the three evaluated systems (built, green)
```

So: the **platform deploys the cluster** (Terraform → EKS), and the **RAG
system becomes the "ML model as a production API with monitoring"**: deployed
on that cluster with Prometheus scraping its `/prometheus` endpoint and alert
rules on *domain* signals (abstention rate, schema violations, cost burn),
which is what separates ML monitoring from generic CPU dashboards. Both of
your asks are satisfied, in the right places.

## 2. Feasibility verdict: honest and specific

**Technically: yes, and it is now built.** `platform/terraform` passes
`terraform validate` against the real AWS provider; `platform/k8s` passes
kubeconform; the deploy workflow uses GitHub OIDC (no long-lived AWS keys).
What I cannot do from this sandbox is run `terraform apply` - that requires
your AWS account. From your side it is: bootstrap the state bucket, `terraform
apply` (~15 min for EKS), run the deploy workflow. The runbook is in
`platform/README.md`.

**Financially: feasible only with discipline.** This stack costs roughly
**$150–175/month while running** (EKS control plane ~$73, single NAT ~$35+data,
2 spot t3.medium ~$18, EBS/misc ~$10, ALB ~$20 if you add ingress). On a Lagos
budget that is real money, so the runbook treats the cluster as **ephemeral**:
apply → deploy → record the demo, screenshot the Grafana dashboards, capture
the audit trail → `terraform destroy` same day. Total cost per demo cycle:
under $10. The *artifact* (repo + recordings) is permanent; the *cluster* is
disposable. Never leave it running idle.

**Strategically: yes, with one warning.** A high-ticket offer built on this is
credible, but the constraint is not technical capability, it's proof and
distribution. The plan below is sequenced around acquiring proof first.

## 3. The high-ticket strategy

### Positioning
"**Production-grade AI systems on cost-disciplined AWS infrastructure - for
organizations that cannot afford to burn cloud budget finding out what works.**"

This is differentiated, and it is authentically yours: 15+ years of
infrastructure at a multilateral institution + applied ML + the emerging-market
cost discipline that most US consultants have never needed. Every artifact in
this repo enacts the positioning: evals as contracts, spot capacity, single-NAT
tradeoffs, budget-clamped agents, $0 CI.

### Who buys (in order of realism)
1. **Remote-first US/EU startups (10–80 people)** adding AI features without a
   platform team - highest rates, found through content + network.
2. **African fintechs and financial-sector institutions**: your WAIFEM
   credibility is strongest here; sales cycles longer, budgets real.
3. **Development-sector / multilateral-adjacent orgs**: you speak their
   language natively; procurement is slow but contracts are sizable.

### The offer ladder (each rung de-risks the next for the buyer)

| Rung | Offer | Deliverable | Price band | Basis |
|---|---|---|---|---|
| 1 | **AI Readiness & Infra Audit** (2 wks) | Written report in the style of `ENGINEERING_REVIEW.md` + `SECURITY_AUDIT.md`: findings with severities, fixes ranked by cost | $1.5k–4k (Africa) / $4k–8k (US remote) | Fixed-scope, low-risk entry; you already have the format |
| 2 | **Reference Implementation** (4–8 wks) | One production AI system (RAG/agent/pipeline pattern) + the platform layer + monitoring + eval suite + handover runbook - i.e., exactly this repo, on their domain | $10k–20k (Africa) / $25k–60k (US) | The three systems are your demo; the platform is your accelerator - "handle the heavy lifting" means you deploy from a proven base, not from scratch |
| 3 | **MLOps Retainer** | Monitoring response, eval-gated deploys, cost reviews, quarterly audit refresh | $1.5k–4k/mo (Africa) / $5k–10k/mo (US) | Recurring revenue; the alert rules and quality scores are the retainer's daily substance |

The ladder is the strategy: audits generate implementation work; every
implementation ends with a retainer proposal; the platform layer means rung-2
margins improve with each client because the heavy lifting amortizes.

### 90-day sequencing (proof before promotion)
- **Days 1–30:** Deploy the platform once, end-to-end, in your own AWS
  account. Record it. Write it up as the third blog post ("Deploying an
  evaluated RAG API to EKS for the cost of a Lagos lunch"). This is the
  demo asset every sales conversation needs.
- **Days 31–60:** Two audits at founding-client pricing (50–60% of band) in
  exchange for named case studies. Source: your direct network, WAIFEM's
  member-institution orbit, and the two blog drafts you already have.
- **Days 61–90:** Convert one audit to a reference implementation. Publish
  the case study. Raise prices to band.

### How this compounds with your existing plans
The platform work is the missing artifact for your **SRE/DevOps/Platform
lane** (Terraform + EKS + OIDC + monitoring is the exact interview surface),
the delivery work generates the independent-income evidence, and shipped
client systems in emerging-market contexts feed the same narrative as
`costlens`: cost-aware production ML where budgets are constrained. One body
of work, three uses. It does **not** replace the costlens sprint - rung-1
audits are sized to run alongside it; rung 2 is a scheduling decision you make
per-opportunity.

### Risks, named
- **Distribution is the bottleneck, not delivery.** Mitigation: the 90-day
  plan spends most of its effort on proof assets, not more code.
- **Solo-consultant key-person risk** on retainers. Mitigation: everything you
  hand over is runbook'd, which is also the sales pitch ("you keep the keys").
- **Price-anchor risk:** starting too low in the African market makes US-band
  pricing awkward later. Mitigation: discount as "founding client," never as
  list price.
- **Scope creep on rung 2.** Mitigation: the eval suite *is* the acceptance
  criterion - contractually. "Done" = suite green on agreed cases. This is a
  genuinely unusual and strong contracting position; use it.

## 4. What was built for this (all validated)

- `platform/terraform/` - VPC (2 AZ, single NAT, documented tradeoff), EKS
  1.31, spot managed node group across two instance types, IRSA OIDC
  provider, ECR with immutable tags + lifecycle policy. `terraform validate` ✅
- `platform/k8s/` - Deployments/Services for all three systems (RAG with
  HPA + PDB, agent single-replica with the queue note, pipeline as CronJob
  with `concurrencyPolicy: Forbid` + query API), probes, resource bounds,
  non-root security context. kubeconform ✅
- `platform/k8s/monitoring/` - ServiceMonitor + PrometheusRule with five
  domain alerts (abstention rate, schema violations, p95, **cost burn rate**,
  target down).
- P1 gained a real `/prometheus` exposition endpoint (hand-rolled, zero new
  dependencies) - verified live; regression gates still green.
- `platform/deploy-workflow.yml` - OIDC role assumption, immutable SHA tags,
  rollout gating.
- `platform/README.md` - bootstrap, apply, deploy, verify, **destroy** runbook
  with the cost sheet.
