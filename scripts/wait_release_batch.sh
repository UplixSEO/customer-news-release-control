#!/usr/bin/env bash
# Poll one exact release batch through the read-only Cloud Build list API.

set -euo pipefail

PHASE="${1:-}"
RELEASE_TAG="${2:-}"
COMMIT_SHA="${3:-}"
PROJECT_ID="${PROJECT_ID:-customer-news-475010}"
REGION="${REGION:-europe-west1}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-60}"
SLEEP_SECONDS="${SLEEP_SECONDS:-15}"

[[ "${PHASE}" == "pending" || "${PHASE}" == "terminal" || "${PHASE}" == "success" ]] || {
  echo "ERROR: phase must be pending, terminal, or success." >&2
  exit 1
}
[[ "${RELEASE_TAG}" =~ ^customer-news-release/[1-9][0-9]*-(promote|rollback)-[0-9a-f]{40}$ ]] || {
  echo "ERROR: invalid release tag." >&2
  exit 1
}
[[ "${COMMIT_SHA}" =~ ^[0-9a-f]{40}$ ]] || {
  echo "ERROR: invalid commit SHA." >&2
  exit 1
}

for attempt in $(seq 1 "${MAX_ATTEMPTS}"); do
  builds="$(gcloud builds list \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --filter="substitutions.TAG_NAME=${RELEASE_TAG} AND substitutions.COMMIT_SHA=${COMMIT_SHA}" \
    --format=json \
    --limit=1000 \
    --page-size=1000 | jq -c \
      --arg tag "${RELEASE_TAG}" \
      --arg sha "${COMMIT_SHA}" '[
        .[]
        | select(.substitutions.TAG_NAME == $tag)
        | select(.substitutions.COMMIT_SHA == $sha)
        | {id, status, trigger: .substitutions.TRIGGER_NAME}
      ]')"
  set +e
  result="$(python scripts/release_build_state.py --phase "${PHASE}" <<<"${builds}")"
  ready=$?
  set -e
  if [[ "${ready}" == "0" ]]; then
    if [[ -n "${OUTPUT_PATH:-}" ]]; then
      printf '%s\n' "${builds}" > "${OUTPUT_PATH}"
    fi
    printf '%s\n' "${result}"
    exit 0
  fi
  if [[ "${ready}" == "2" ]]; then
    echo "ERROR: exact release batch reached terminal failure: ${result}" >&2
    exit 1
  fi
  if [[ "${attempt}" == "${MAX_ATTEMPTS}" ]]; then
    echo "ERROR: exact release batch did not reach ${PHASE}: ${result}" >&2
    exit 1
  fi
  echo "Waiting for exact release batch ${RELEASE_TAG} (${attempt}/${MAX_ATTEMPTS})" >&2
  sleep "${SLEEP_SECONDS}"
done
