#!/usr/bin/env bash
# Validate an exact private-upstream release tag and its successful guard run.

set -euo pipefail

UPSTREAM_REPOSITORY="${UPSTREAM_REPOSITORY:-UplixSEO/Uplix-Agents}"
RELEASE_TAG="${RELEASE_TAG:-${1:-}}"

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "ERROR: GH_TOKEN from the read-only upstream GitHub App is required." >&2
  exit 1
fi
if [[ ! "${RELEASE_TAG}" =~ ^customer-news-release/([1-9][0-9]*)-(promote|rollback)-([0-9a-f]{40})$ ]]; then
  echo "ERROR: release_tag must encode run id, mode, and exact SHA." >&2
  exit 1
fi

RUN_ID="${BASH_REMATCH[1]}"
RELEASE_MODE="${BASH_REMATCH[2]}"
EXPECTED_SHA="${BASH_REMATCH[3]}"

ref_json="$(gh api "repos/${UPSTREAM_REPOSITORY}/git/ref/tags/${RELEASE_TAG}")"
TAG_OBJECT_TYPE="$(jq -r '.object.type' <<<"${ref_json}")"
TAG_OBJECT_SHA="$(jq -r '.object.sha' <<<"${ref_json}")"
if [[ "${TAG_OBJECT_TYPE}" != "tag" || ! "${TAG_OBJECT_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: upstream release candidate must be an annotated tag." >&2
  exit 1
fi

tag_json="$(gh api "repos/${UPSTREAM_REPOSITORY}/git/tags/${TAG_OBJECT_SHA}")"
tag_commit_type="$(jq -r '.object.type' <<<"${tag_json}")"
tag_commit_sha="$(jq -r '.object.sha' <<<"${tag_json}")"
if [[ "${tag_commit_type}" != "commit" || "${tag_commit_sha}" != "${EXPECTED_SHA}" ]]; then
  echo "ERROR: annotated tag target does not match the encoded SHA." >&2
  exit 1
fi

run_json="$(gh api "repos/${UPSTREAM_REPOSITORY}/actions/runs/${RUN_ID}")"
jq -e \
  --arg sha "${EXPECTED_SHA}" \
  --arg mode "${RELEASE_MODE}" \
  --arg repository "${UPSTREAM_REPOSITORY}" '
    .head_branch == "main"
    and (($mode == "promote" and .event == "push" and .head_sha == $sha)
      or ($mode == "rollback" and .event == "workflow_dispatch"))
    and .status == "completed"
    and .conclusion == "success"
    and .path == ".github/workflows/customer-news-cloudbuild-guard.yml"
    and .head_repository.full_name == $repository
    and .head_repository.private == true
  ' <<<"${run_json}" >/dev/null || {
    echo "ERROR: upstream workflow run is not the successful private main guard for this SHA." >&2
    exit 1
  }

pulls_json="$(gh api \
  -H 'Accept: application/vnd.github+json' \
  "repos/${UPSTREAM_REPOSITORY}/commits/${EXPECTED_SHA}/pulls")"
promotion_pr_json="$(jq -c \
  --arg sha "${EXPECTED_SHA}" \
  --arg repository "${UPSTREAM_REPOSITORY}" '
    [ .[]
      | select(
          .state == "closed"
          and .merged_at != null
          and .merge_commit_sha == $sha
          and .base.ref == "main"
          and .head.ref == "dev"
          and .base.repo.full_name == $repository
          and .head.repo.full_name == $repository
        )
    ]
  ' <<<"${pulls_json}")"
if [[ "$(jq 'length' <<<"${promotion_pr_json}")" != "1" ]]; then
  echo "ERROR: exact SHA must be the unique merged Uplix-Agents dev-to-main PR commit." >&2
  exit 1
fi
PROMOTION_PR_NUMBER="$(jq -r '.[0].number' <<<"${promotion_pr_json}")"

printf 'sha=%s\nrun_id=%s\nmode=%s\nrelease_tag=%s\npromotion_pr=%s\n' \
  "${EXPECTED_SHA}" "${RUN_ID}" "${RELEASE_MODE}" "${RELEASE_TAG}" "${PROMOTION_PR_NUMBER}"
