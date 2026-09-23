# Platform Runbook — Terraform → EKS → Deployed, Monitored Systems

The infrastructure layer under the three portfolio systems. Everything here is
validated (`terraform validate` against the AWS provider ~>5.70; kubeconform on
all manifests) and designed to be **ephemeral**: apply, demo, record, destroy.

## Cost sheet (eu-west-1, while running)

| Item | ~$/month |
|---|---|
| EKS control plane | 73 |
| NAT gateway (single, by design) | 35 + data |
| 2× t3.medium SPOT nodes | 18 |
| EBS + misc | 10 |
| **Total (no ingress LB)** | **~140–160** |

Per demo cycle (apply → record → destroy same day): **under $10**.
Rule: the cluster is disposable; the repo and recordings are the asset.

## 0. One-time bootstrap (state backend)

```bash
aws s3api create-bucket --bucket waifem-ml-platform-tfstate \
  --region eu-west-1 --create-bucket-configuration LocationConstraint=eu-west-1
aws s3api put-bucket-versioning --bucket waifem-ml-platform-tfstate \
  --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name waifem-ml-platform-tflock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH --billing-mode PAY_PER_REQUEST
```
Then uncomment the `backend "s3"` block in `terraform/versions.tf`.

## 1. Provision (~15 min, mostly EKS)

```bash
cd platform/terraform
terraform init
terraform plan          # read it. every resource should be explainable.
terraform apply
aws eks update-kubeconfig --region eu-west-1 --name ml-platform   # from outputs
kubectl get nodes       # expect 2 spot nodes Ready
```

## 2. Monitoring stack

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  --set grafana.adminPassword="$(openssl rand -base64 16 | tee /tmp/grafana-pass)"
```

## 3. Build, push, deploy

Preferred: run the GitHub Actions workflow (`deploy-workflow.yml` → copy to
`.github/workflows/deploy.yml`; create the OIDC deploy role and set
`AWS_DEPLOY_ROLE_ARN`). Manual equivalent:

```bash
REG=$(terraform -chdir=platform/terraform output -json ecr_repository_urls | jq -r '.["rag-api"]' | cut -d/ -f1)
aws ecr get-login-password | docker login --username AWS --password-stdin "$REG"
TAG=$(git rev-parse --short=12 HEAD)
for p in "project1-rag-failure-analysis rag-api" "project2-agent-loop-design asset-agent" "project3-data-pipeline ticket-pipeline"; do
  set -- $p; docker build -t "$REG/$2:$TAG" "$1" && docker push "$REG/$2:$TAG"
done
kubectl apply -f platform/k8s/namespace.yaml
kubectl -n ml-platform create secret generic anthropic --from-literal=api-key="$ANTHROPIC_API_KEY"  # optional; omit -> mock backends
for f in platform/k8s/*.yaml platform/k8s/monitoring/*.yaml; do
  sed -e "s|ECR_REGISTRY|$REG|g" -e "s|IMAGE_TAG|$TAG|g" "$f" | kubectl apply -f -
done
kubectl -n ml-platform rollout status deploy/rag-api
```

## 4. Verify the story you'll demo

```bash
kubectl -n ml-platform port-forward svc/rag-api 8080:80 &
curl -X POST localhost:8080/ask -H 'Content-Type: application/json' \
  -d '{"query":"What fuse protects the battery bank?"}'
curl localhost:8080/prometheus        # the series the alerts watch
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80 &
# Grafana: import a dashboard over rag_abstention_rate, rag_latency_p95_ms,
# rag_cost_usd_total, rag_schema_violations_total. Screenshot it.
kubectl -n ml-platform create job --from=cronjob/ticket-ingest ingest-demo   # run the pipeline once
```

Demo narrative in one line per system: *model API with domain alerts* (RAG),
*audited bounded agent* (`/runs/{id}`), *scheduled quality-scored ingest*
(pipeline CronJob).

## 5. Destroy (same day)

```bash
helm uninstall kube-prometheus-stack -n monitoring
kubectl delete ns ml-platform monitoring        # releases any LBs first
terraform -chdir=platform/terraform destroy
# billing console next day: confirm $0 accrual. Every time.
```

## Design decisions (interview surface)

Single NAT (HA nobody needs at demo scale, −$35/mo per extra AZ) · spot
node group across two instance types (stateless workloads; ~70% off) ·
IRSA + GitHub OIDC (zero long-lived credentials anywhere) · immutable ECR
tags pinned to git SHA (a tag is a contract) · `concurrencyPolicy: Forbid`
on ingest (SQLite is single-writer; the schedule respects the storage) ·
domain alerts over infra alerts (abstention/cost-burn beat CPU% for a model
API) · agent kept at 1 replica with the queue migration named in
DEPLOYMENT.md (honest scaling story beats a fake one).
