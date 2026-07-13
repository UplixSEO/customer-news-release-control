#!/usr/bin/env bash
# Approve exactly the fixed pending Cloud Build batch for one release tag/SHA.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-customer-news-475010}"
REGION="${REGION:-europe-west1}"
AUTHORITIES_FILE="${AUTHORITIES_FILE:-config/release-authorities.txt}"
RELEASE_TAG="${RELEASE_TAG:-}"
COMMIT_SHA="${COMMIT_SHA:-}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-20}"
SLEEP_SECONDS="${SLEEP_SECONDS:-15}"

[[ "${RELEASE_TAG}" =~ ^customer-news-release/[1-9][0-9]*-(promote|rollback)-[0-9a-f]{40}$ ]] || {
  echo "ERROR: invalid RELEASE_TAG." >&2
  exit 1
}
[[ "${COMMIT_SHA}" =~ ^[0-9a-f]{40}$ ]] || {
  echo "ERROR: invalid COMMIT_SHA." >&2
  exit 1
}

mapfile -t EXPECTED_AUTHORITIES < <(
  sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "${AUTHORITIES_FILE}" | sort -u
)
if [[ "${#EXPECTED_AUTHORITIES[@]}" != "17" ]]; then
  echo "ERROR: authority inventory must contain exactly 17 unique names." >&2
  exit 1
fi
expected_json="$(printf '%s\n' "${EXPECTED_AUTHORITIES[@]}" | jq -R . | jq -cs .)"

release_builds='[]'
for attempt in $(seq 1 "${MAX_ATTEMPTS}"); do
  all_builds="$(gcloud builds list \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --filter="substitutions.TAG_NAME=${RELEASE_TAG} AND substitutions.COMMIT_SHA=${COMMIT_SHA}" \
    --format=json \
    --limit=100)"
  release_builds="$(jq -c \
    --arg tag "${RELEASE_TAG}" \
    --arg sha "${COMMIT_SHA}" '
      [ .[]
        | select(.substitutions.TAG_NAME == $tag)
        | select(.substitutions.COMMIT_SHA == $sha)
        | {id, status, trigger: .substitutions.TRIGGER_NAME}
      ]
    ' <<<"${all_builds}")"

  unknown="$(jq -c --argjson expected "${expected_json}" \
    '[.[] | .trigger as $trigger | select(($expected | index($trigger)) | not)]' <<<"${release_builds}")"
  if [[ "$(jq 'length' <<<"${unknown}")" != "0" ]]; then
    echo "ERROR: release tag produced unknown builds: ${unknown}" >&2
    exit 1
  fi

  ready=true
  for trigger in "${EXPECTED_AUTHORITIES[@]}"; do
    matching="$(jq -c --arg trigger "${trigger}" '[.[] | select(.trigger == $trigger)]' <<<"${release_builds}")"
    if [[ "$(jq 'length' <<<"${matching}")" != "1" ]]; then
      ready=false
      continue
    fi
    status="$(jq -r '.[0].status' <<<"${matching}")"
    if [[ "${status}" != "PENDING" ]]; then
      echo "ERROR: ${trigger} is ${status}, expected PENDING before approval." >&2
      exit 1
    fi
  done

  if [[ "${ready}" == "true" && "$(jq 'length' <<<"${release_builds}")" == "17" ]]; then
    break
  fi
  if [[ "${attempt}" == "${MAX_ATTEMPTS}" ]]; then
    echo "ERROR: timed out waiting for the exact 17-build pending batch: ${release_builds}" >&2
    exit 1
  fi
  echo "Waiting for exact pending release batch (${attempt}/${MAX_ATTEMPTS})."
  sleep "${SLEEP_SECONDS}"
done

for trigger in "${EXPECTED_AUTHORITIES[@]}"; do
  build_id="$(jq -r --arg trigger "${trigger}" '.[] | select(.trigger == $trigger) | .id' <<<"${release_builds}")"
  current="$(gcloud builds describe "${build_id}" \
    --project="${PROJECT_ID}" --region="${REGION}" --format=json)"
  jq -e \
    --arg tag "${RELEASE_TAG}" \
    --arg sha "${COMMIT_SHA}" \
    --arg trigger "${trigger}" '
      .status == "PENDING"
      and .substitutions.TAG_NAME == $tag
      and .substitutions.COMMIT_SHA == $sha
      and .substitutions.TRIGGER_NAME == $trigger
    ' <<<"${current}" >/dev/null || {
      echo "ERROR: build ${build_id} drifted before approval." >&2
      exit 1
    }
  gcloud beta builds approve \
    "projects/${PROJECT_ID}/locations/${REGION}/builds/${build_id}" \
    --comment="Protected Customer News release ${COMMIT_SHA}" \
    --quiet >/dev/null
  printf 'approved\t%s\t%s\n' "${trigger}" "${build_id}"
done
