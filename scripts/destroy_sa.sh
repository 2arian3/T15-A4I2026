#!/usr/bin/env bash
# Removes the Curbside service account and the grants setup_sa.sh made.
# Run `make destroy` first: a deployed agent running as this SA stops working.
#
# Usage (normally via `make destroy-sa`):
#   PROJECT_ID=... SA_NAME=... [DATASET=a4i_health] bash scripts/destroy_sa.sh
#   DRY_RUN=1 ...   prints the commands without running them.
set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${SA_NAME:?SA_NAME is required}"
DATASET="${DATASET:-a4i_health}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DRY_RUN="${DRY_RUN:-0}"

# Keep in sync with PROJECT_ROLES in setup_sa.sh.
PROJECT_ROLES=(
  roles/aiplatform.user
  roles/bigquery.jobUser
  roles/mcp.toolUser
  roles/serviceusage.serviceUsageConsumer
  roles/logging.logWriter
  roles/monitoring.metricWriter
  roles/cloudtrace.agent
)

run() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}
info() { printf '==> %s\n' "$*"; }

if [[ "${DRY_RUN}" != "1" ]] && ! gcloud iam service-accounts describe "${SA_EMAIL}" \
     --project="${PROJECT_ID}" >/dev/null 2>&1; then
  info "${SA_EMAIL} does not exist; nothing to do."
  exit 0
fi

info "Removing project roles from ${SA_EMAIL}"
for role in "${PROJECT_ROLES[@]}"; do
  run gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" --role="${role}" \
    --condition=None --quiet --format=none || true
done

info "Revoking dataset access on ${DATASET}"
run bq --project_id="${PROJECT_ID}" query --use_legacy_sql=false --quiet \
  "REVOKE \`roles/bigquery.dataViewer\` ON SCHEMA \`${PROJECT_ID}.${DATASET}\` FROM \"serviceAccount:${SA_EMAIL}\"" \
  || true

info "Deleting service account"
run gcloud iam service-accounts delete "${SA_EMAIL}" --project="${PROJECT_ID}" --quiet
info "Done."
