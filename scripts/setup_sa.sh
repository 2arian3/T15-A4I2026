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
AUDIT_DATASET="${AUDIT_DATASET:-a4i_audit}"
LOCATION="${LOCATION:-US}"
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
  cloudbuild.googleapis.com          # Agent Runtime builds the container image
  storage.googleapis.com
  logging.googleapis.com
  monitoring.googleapis.com
  run.googleapis.com
  artifactregistry.googleapis.com
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
run gcloud services enable "${APIS[@]}" --project="${PROJECT_ID}" --quiet

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

info "Creating the audit dataset if missing"
if [[ "${DRY_RUN}" == "1" ]] || ! bq --project_id="${PROJECT_ID}" show --dataset \
     --format=none "${PROJECT_ID}:${AUDIT_DATASET}" >/dev/null 2>&1; then
  run bq --project_id="${PROJECT_ID}" mk --dataset --location="${LOCATION}" "${PROJECT_ID}:${AUDIT_DATASET}"
fi

for identity in planner auditor; do
  tool_name="curbside-${identity}"
  tool_email="${tool_name}@${PROJECT_ID}.iam.gserviceaccount.com"
  if [[ "${DRY_RUN}" == "1" ]] || ! gcloud iam service-accounts describe "${tool_email}" \
       --project="${PROJECT_ID}" >/dev/null 2>&1; then
    run gcloud iam service-accounts create "${tool_name}" --project="${PROJECT_ID}" \
      --display-name="Curbside ${identity}" --quiet
    [[ "${DRY_RUN}" == "1" ]] || sleep 10
  fi
  run gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${tool_email}" --role=roles/bigquery.jobUser --condition=None --quiet --format=none
  # Impersonation is scoped to these identities, never project-wide.
  run gcloud iam service-accounts add-iam-policy-binding "${tool_email}" \
    --project="${PROJECT_ID}" --member="serviceAccount:${SA_EMAIL}" \
    --role=roles/iam.serviceAccountTokenCreator --quiet --format=none
  datasets=("${DATASET}")
  [[ "${identity}" != auditor ]] || datasets+=("${AUDIT_DATASET}")
  for dataset in "${datasets[@]}"; do
    if [[ "${DRY_RUN}" == "1" ]] || bq --project_id="${PROJECT_ID}" show --dataset \
         --format=none "${PROJECT_ID}:${dataset}" >/dev/null 2>&1; then
      run bq --project_id="${PROJECT_ID}" query --use_legacy_sql=false --quiet \
        "GRANT \`${DATASET_ROLE}\` ON SCHEMA \`${PROJECT_ID}.${dataset}\` TO \"serviceAccount:${tool_email}\""
    else
      warn "Dataset ${dataset} missing; load health data and rerun make setup."
    fi
  done
done

# Migrate the old single-identity setup: runtime gets no direct dataset access.
for dataset in "${DATASET}" "${AUDIT_DATASET}"; do
  if [[ "${DRY_RUN}" == "1" ]] || bq --project_id="${PROJECT_ID}" show --dataset \
       --format=none "${PROJECT_ID}:${dataset}" >/dev/null 2>&1; then
    run bq --project_id="${PROJECT_ID}" query --use_legacy_sql=false --quiet \
      "REVOKE \`${DATASET_ROLE}\` ON SCHEMA \`${PROJECT_ID}.${dataset}\` FROM \"serviceAccount:${SA_EMAIL}\""
  fi
done

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


# Local user ADC follows the same runtime -> tool impersonation chain, keylessly.
if [[ -n "${DEPLOYER}" ]]; then
  run gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
    --project="${PROJECT_ID}" --member="${DEPLOYER}" \
    --role=roles/iam.serviceAccountTokenCreator --quiet --format=none
fi

info "Done. IAM changes can take a minute or two to propagate."
