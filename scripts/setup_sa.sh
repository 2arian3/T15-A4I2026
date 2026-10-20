#!/usr/bin/env bash
# Creates the Curbside runtime service account and grants it what it needs.
#
# Safe to run more than once: every step checks first or is an idempotent grant.
#
# Usage (normally via `make setup`):
#   PROJECT_ID=... SA_NAME=... [DATASET=a4i_health] [DEPLOYER=user:me@x.com] \
#     bash scripts/setup_sa.sh
#   DRY_RUN=1 ...   prints the commands without running them.
set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${SA_NAME:?SA_NAME is required}"
DATASET="${DATASET:-a4i_health}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DRY_RUN="${DRY_RUN:-0}"

# APIs the agent and its deploy need.
APIS=(
  aiplatform.googleapis.com          # Gemini via Vertex AI + Agent Runtime
  bigquery.googleapis.com            # data + BigQuery's managed MCP server
  iam.googleapis.com
  iamcredentials.googleapis.com
  cloudresourcemanager.googleapis.com
  serviceusage.googleapis.com
  cloudbuild.googleapis.com          # Agent Runtime & Cloud Run build the container image
  run.googleapis.com                 # Cloud Run hosts the Curbside Leaflet frontend
  artifactregistry.googleapis.com    # Container registry for Cloud Run source deploys
  storage.googleapis.com
  logging.googleapis.com
  monitoring.googleapis.com
  cloudtrace.googleapis.com
)

# Project-level roles for the runtime service account (least privilege).
PROJECT_ROLES=(
  roles/aiplatform.user                    # call Gemini, use Agent Runtime sessions
  roles/bigquery.jobUser                   # run query jobs
  roles/mcp.toolUser                       # call BigQuery's managed MCP server
  roles/serviceusage.serviceUsageConsumer  # consume project APIs
  roles/logging.logWriter
  roles/monitoring.metricWriter
  roles/cloudtrace.agent
)

# Dataset-level role: read only the challenge dataset, nothing else.
DATASET_ROLE="roles/bigquery.dataViewer"

run() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

info() { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }

info "Project         : ${PROJECT_ID}"
info "Service account : ${SA_EMAIL}"

info "Enabling APIs (no-op for ones already on)"
run gcloud services enable "${APIS[@]}" --project="${PROJECT_ID}"

info "Creating service account if missing"
if [[ "${DRY_RUN}" != "1" ]] && gcloud iam service-accounts describe "${SA_EMAIL}" \
     --project="${PROJECT_ID}" >/dev/null 2>&1; then
  info "${SA_EMAIL} already exists"
else
  run gcloud iam service-accounts create "${SA_NAME}" \
    --project="${PROJECT_ID}" \
    --display-name="Curbside agent runtime" \
    --description="Runtime identity for the Curbside ADK agent on Agent Runtime"
  # New service accounts take a few seconds to become visible to IAM.
  [[ "${DRY_RUN}" == "1" ]] || sleep 10
fi

info "Granting project roles"
for role in "${PROJECT_ROLES[@]}"; do
  run gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${role}" \
    --condition=None \
    --quiet --format=none
done

info "Granting ${DATASET_ROLE} on dataset ${DATASET}"
if [[ "${DRY_RUN}" == "1" ]] || bq --project_id="${PROJECT_ID}" show --dataset \
     --format=none "${PROJECT_ID}:${DATASET}" >/dev/null 2>&1; then
  # BigQuery DCL: idempotent, and scoped to this one dataset.
  run bq --project_id="${PROJECT_ID}" query --use_legacy_sql=false --quiet \
    "GRANT \`${DATASET_ROLE}\` ON SCHEMA \`${PROJECT_ID}.${DATASET}\` TO \"serviceAccount:${SA_EMAIL}\""
else
  warn "Dataset ${PROJECT_ID}:${DATASET} not found. Load it (bash scripts/load.sh),"
  warn "then re-run 'make setup' to grant the agent read access."
fi

# The person (or CI) running `make deploy` must be able to act as the SA.
DEPLOYER="${DEPLOYER:-}"
if [[ -z "${DEPLOYER}" && "${DRY_RUN}" != "1" ]]; then
  account="$(gcloud config get-value account 2>/dev/null || true)"
  if [[ -n "${account}" ]]; then
    if [[ "${account}" == *.gserviceaccount.com ]]; then
      DEPLOYER="serviceAccount:${account}"
    else
      DEPLOYER="user:${account}"
    fi
  fi
fi
if [[ -n "${DEPLOYER}" ]]; then
  info "Granting roles/iam.serviceAccountUser on the SA to ${DEPLOYER}"
  run gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
    --project="${PROJECT_ID}" \
    --member="${DEPLOYER}" \
    --role="roles/iam.serviceAccountUser" \
    --quiet --format=none
else
  warn "No gcloud account found; skipping roles/iam.serviceAccountUser for the deployer."
fi

info "Done. IAM changes can take a minute or two to propagate."
