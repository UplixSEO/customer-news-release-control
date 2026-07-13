#!/usr/bin/env bash
# Collect normalized ancestor->descendant facts through GitHub's native compare API.

set -euo pipefail

CANDIDATE_SHA="${1:?candidate SHA is required}"
CURRENT_HEAD_SHA="${2:?current-head SHA is required}"
LEDGER_PATH="${3:?ledger JSON path is required}"
UPSTREAM_REPOSITORY="${UPSTREAM_REPOSITORY:-UplixSEO/Uplix-Agents}"

[[ -n "${GH_TOKEN:-}" ]] || {
  echo "ERROR: GH_TOKEN from the read-only upstream App is required." >&2
  exit 1
}

mapfile -t shas < <(
  jq -r '.[].sha' "${LEDGER_PATH}"
  printf '%s\n%s\n' "${CANDIDATE_SHA}" "${CURRENT_HEAD_SHA}"
)
mapfile -t unique_shas < <(printf '%s\n' "${shas[@]}" | sort -u)

ancestry='{}'
for ancestor in "${unique_shas[@]}"; do
  for descendant in "${CANDIDATE_SHA}" "${CURRENT_HEAD_SHA}"; do
    [[ "${ancestor}" =~ ^[0-9a-f]{40}$ && "${descendant}" =~ ^[0-9a-f]{40}$ ]] || continue
    key="${ancestor}..${descendant}"
    if [[ "${ancestor}" == "${descendant}" ]]; then
      proven=true
    else
      compare_status="$(gh api \
        "repos/${UPSTREAM_REPOSITORY}/compare/${ancestor}...${descendant}" \
        --jq '.status')"
      if [[ "${compare_status}" == "ahead" || "${compare_status}" == "identical" ]]; then
        proven=true
      else
        proven=false
      fi
    fi
    ancestry="$(jq -c --arg key "${key}" --argjson proven "${proven}" \
      '. + {($key): $proven}' <<<"${ancestry}")"
  done
done
jq -S . <<<"${ancestry}"
