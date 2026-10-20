#!/usr/bin/env bash
# Builds and deploys the fixed-query MCP server; run only after deployment approval.
set -euo pipefail
: "${PROJECT_ID:?PROJECT_ID is required}"
: "${SA_NAME:?SA_NAME is required}"
REGION="${REGION:-us-central1}"
DATASET="${DATASET:-a4i_health}"
DRY_RUN="${DRY_RUN:-0}"
TOOLBOX_SERVICE="${TOOLBOX_SERVICE:-curbside-toolbox}"
DEFAULT_TOOLBOX_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/curbside/toolbox:1.14.0"
TOOLBOX_IMAGE="${TOOLBOX_IMAGE:-${DEFAULT_TOOLBOX_IMAGE}}"
run() {
  if [[ "${DRY_RUN}" == 1 ]]; then printf '+ %s\n' "$*"; else "$@"; fi
}
# Provision only the repository owned by this deploy; custom images manage their own.
if [[ "${TOOLBOX_IMAGE}" == "${DEFAULT_TOOLBOX_IMAGE}" ]]; then
  if [[ "${DRY_RUN}" == 1 ]]; then
    run gcloud artifacts repositories create curbside --repository-format=docker \
      --project="${PROJECT_ID}" --location="${REGION}" --quiet
  else
    repo_error="$(mktemp)"
    trap 'rm -f "${repo_error}"' EXIT
    if repo_format="$(gcloud artifacts repositories describe curbside \
        --project="${PROJECT_ID}" --location="${REGION}" --format='value(format)' \
        --quiet 2>"${repo_error}")"; then
      if [[ "${repo_format}" != DOCKER ]]; then
        echo "Repository curbside exists but is not a Docker repository." >&2
        exit 1
      fi
    elif grep -q NOT_FOUND "${repo_error}"; then
      run gcloud artifacts repositories create curbside --repository-format=docker \
        --project="${PROJECT_ID}" --location="${REGION}" --quiet
    else
      cat "${repo_error}" >&2
      exit 1
    fi
    rm -f "${repo_error}"
    trap - EXIT
  fi
fi
run gcloud builds submit toolbox --tag="${TOOLBOX_IMAGE}" --project="${PROJECT_ID}" --region="${REGION}" --quiet
run gcloud run deploy "${TOOLBOX_SERVICE}" --image="${TOOLBOX_IMAGE}" \
  --service-account="curbside-planner@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project="${PROJECT_ID}" --region="${REGION}" --quiet \
  --no-allow-unauthenticated --invoker-iam-check \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},BQ_DATASET=${DATASET}"
run gcloud run services add-iam-policy-binding "${TOOLBOX_SERVICE}" \
  --project="${PROJECT_ID}" --region="${REGION}" --quiet \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" --role=roles/run.invoker
run gcloud run services describe "${TOOLBOX_SERVICE}" --project="${PROJECT_ID}" \
  --region="${REGION}" --format='value(status.url)' --quiet
