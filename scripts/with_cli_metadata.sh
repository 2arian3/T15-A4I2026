#!/usr/bin/env bash
# Run a command with deployment_metadata.json swapped to an env-specific file.
#
# agents-cli always reads and writes deployment_metadata.json. Production keeps
# that filename; sandbox deploys copy it aside so they cannot update curbside.
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <metadata-file> <command> [args...]" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

META="$1"
shift
PROD="deployment_metadata.json"

if [[ "$META" == "$PROD" || "$META" == "./$PROD" ]]; then
  exec "$@"
fi

STASH="$(mktemp)"
RESTORE_PROD=0
if [[ -f "$PROD" ]]; then
  cp "$PROD" "$STASH"
  RESTORE_PROD=1
fi

cleanup() {
  status=$?
  if [[ -f "$PROD" ]]; then
    cp "$PROD" "$META"
  fi
  if [[ "$RESTORE_PROD" -eq 1 ]]; then
    cp "$STASH" "$PROD"
  else
    rm -f "$PROD"
  fi
  rm -f "$STASH"
  exit "$status"
}
trap cleanup EXIT INT TERM

rm -f "$PROD"
if [[ -f "$META" ]]; then
  cp "$META" "$PROD"
fi

"$@"
