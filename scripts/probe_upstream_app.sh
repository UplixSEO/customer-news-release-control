#!/usr/bin/env bash
# Prove the upstream GitHub App is installed on one private repository with read-only permissions.

set -euo pipefail

UPSTREAM_REPOSITORY="${UPSTREAM_REPOSITORY:-UplixSEO/Uplix-Agents}"
DENIED_REPOSITORY="${DENIED_REPOSITORY:-UplixSEO/uplixOS}"
APP_ENDPOINT="${APP_ENDPOINT:-apps/uplix-customer-news-release-proof}"
EXPECTED_APP_ID="${EXPECTED_APP_ID:-4290359}"
EXPECTED_PERMISSIONS='{"actions":"read","contents":"read","metadata":"read","pull_requests":"read"}'

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "ERROR: GH_TOKEN from the upstream GitHub App is required." >&2
  exit 1
fi

app_json="$(gh api "${APP_ENDPOINT}")"
jq -e --argjson expected "${EXPECTED_PERMISSIONS}" --argjson app_id "${EXPECTED_APP_ID}" '
  .id == $app_id
  and .slug == "uplix-customer-news-release-proof"
  and .permissions == $expected
  and (.events | length) == 0
' <<<"${app_json}" >/dev/null || {
  echo "ERROR: GitHub App identity or permissions drifted." >&2
  exit 1
}

mapfile -t repositories < <(
  gh api installation/repositories --paginate --jq '.repositories[].full_name'
)
if [[ "${#repositories[@]}" != "1" || "${repositories[0]}" != "${UPSTREAM_REPOSITORY}" ]]; then
  echo "ERROR: GitHub App repository scope is not exactly the private upstream." >&2
  exit 1
fi

gh api "repos/${UPSTREAM_REPOSITORY}/contents/.github/workflows/customer-news-cloudbuild-guard.yml" \
  --jq '.sha' >/dev/null
gh api "repos/${UPSTREAM_REPOSITORY}/actions/workflows/customer-news-cloudbuild-guard.yml" \
  --jq '.path' >/dev/null
gh api "repos/${UPSTREAM_REPOSITORY}/pulls?state=closed&per_page=1" \
  --jq 'length' >/dev/null

if gh api "repos/${DENIED_REPOSITORY}" >/dev/null 2>&1; then
  echo "ERROR: GitHub App unexpectedly reached an unselected private repository." >&2
  exit 1
fi

echo "GitHub App boundary passed: one selected private repository, exact read-only permissions."
