#!/usr/bin/env bash
# Thin native gh-api client for the Customer News GitHub Deployment ledger.

set -euo pipefail

OPERATION="${1:-}"
REPOSITORY="${GITHUB_REPOSITORY:-UplixSEO/customer-news-release-control}"
LEDGER_ENVIRONMENT="${LEDGER_ENVIRONMENT:-customer-news-runtime}"

[[ -n "${GH_TOKEN:-}" ]] || {
  echo "ERROR: GH_TOKEN is required." >&2
  exit 1
}

case "${OPERATION}" in
  snapshot)
    deployments="$(gh api --method GET "repos/${REPOSITORY}/deployments" \
      -f environment="${LEDGER_ENVIRONMENT}" -f per_page=100)"
    entries='[]'
    while IFS= read -r deployment; do
      [[ -n "${deployment}" ]] || continue
      deployment_id="$(jq -r '.id' <<<"${deployment}")"
      status="$(gh api --method GET \
        "repos/${REPOSITORY}/deployments/${deployment_id}/statuses" \
        -f per_page=1 --jq '.[0] // {state:"pending",description:""}')"
      entry="$(jq -cn \
        --argjson deployment "${deployment}" \
        --argjson status "${status}" '
          {
            deployment_id: $deployment.id,
            sha: $deployment.payload.upstream_sha,
            release_tag: $deployment.payload.release_tag,
            mode: $deployment.payload.mode,
            state: $status.state,
            superseded_by: (
              ($status.description // "")
              | capture("superseded_by=(?<sha>[0-9a-f]{40})")?.sha
            )
          }
        ')"
      entries="$(jq -c --argjson entry "${entry}" '. + [$entry]' <<<"${entries}")"
    done < <(jq -c '.[] | select(.payload.schema == "customer_news_release_v1")' <<<"${deployments}")
    jq -S . <<<"${entries}"
    ;;

  create)
    : "${UPSTREAM_SHA:?UPSTREAM_SHA is required}"
    : "${RELEASE_TAG:?RELEASE_TAG is required}"
    : "${RELEASE_MODE:?RELEASE_MODE is required}"
    : "${GITHUB_SHA:?GITHUB_SHA is required}"
    [[ "${UPSTREAM_SHA}" =~ ^[0-9a-f]{40}$ ]] || {
      echo "ERROR: UPSTREAM_SHA must be exact 40-hex." >&2
      exit 1
    }
    [[ "${RELEASE_MODE}" == "promote" || "${RELEASE_MODE}" == "rollback" ]] || {
      echo "ERROR: RELEASE_MODE must be promote or rollback." >&2
      exit 1
    }
    parsed="$(sed -nE 's#^customer-news-release/([1-9][0-9]*)-(promote|rollback)-([0-9a-f]{40})$#\1 \2 \3#p' <<<"${RELEASE_TAG}")"
    read -r epoch tag_mode tag_sha <<<"${parsed}"
    [[ -n "${epoch}" ]] || {
      echo "ERROR: RELEASE_TAG is invalid." >&2
      exit 1
    }
    [[ "${tag_mode}" == "${RELEASE_MODE}" && "${tag_sha}" == "${UPSTREAM_SHA}" ]] || {
      echo "ERROR: RELEASE_TAG mode/SHA mismatch." >&2
      exit 1
    }
    jq -n \
      --arg ref "${GITHUB_SHA}" \
      --arg environment "${LEDGER_ENVIRONMENT}" \
      --arg sha "${UPSTREAM_SHA}" \
      --arg tag "${RELEASE_TAG}" \
      --arg mode "${RELEASE_MODE}" \
      --arg epoch "${epoch}" '{
        ref: $ref,
        environment: $environment,
        description: "Customer News exact-SHA convergence",
        auto_merge: false,
        required_contexts: [],
        production_environment: true,
        transient_environment: false,
        payload: {
          schema: "customer_news_release_v1",
          upstream_sha: $sha,
          release_tag: $tag,
          release_epoch: ($epoch | tonumber),
          mode: $mode
        }
      }' | gh api --method POST "repos/${REPOSITORY}/deployments" --input - --jq '.id'
    ;;

  status)
    : "${DEPLOYMENT_ID:?DEPLOYMENT_ID is required}"
    : "${DEPLOYMENT_STATE:?DEPLOYMENT_STATE is required}"
    : "${GITHUB_SERVER_URL:?GITHUB_SERVER_URL is required}"
    case "${DEPLOYMENT_STATE}" in
      pending|queued|in_progress|success|failure|error|inactive) ;;
      *) echo "ERROR: unsupported DEPLOYMENT_STATE." >&2; exit 1 ;;
    esac
    description="${DEPLOYMENT_DESCRIPTION:-Customer News release ${DEPLOYMENT_STATE}}"
    if [[ -n "${SUPERSEDED_BY:-}" ]]; then
      [[ "${SUPERSEDED_BY}" =~ ^[0-9a-f]{40}$ ]] || {
        echo "ERROR: SUPERSEDED_BY must be exact 40-hex." >&2
        exit 1
      }
      description="superseded_by=${SUPERSEDED_BY}"
    fi
    jq -n \
      --arg state "${DEPLOYMENT_STATE}" \
      --arg environment "${LEDGER_ENVIRONMENT}" \
      --arg description "${description}" \
      --arg log_url "${GITHUB_SERVER_URL}/${REPOSITORY}/actions/runs/${GITHUB_RUN_ID:-0}" '{
        state: $state,
        environment: $environment,
        description: $description,
        log_url: $log_url,
        auto_inactive: false
      }' | gh api --method POST \
        "repos/${REPOSITORY}/deployments/${DEPLOYMENT_ID}/statuses" \
        --input - >/dev/null
    ;;

  *)
    echo "Usage: $0 snapshot|create|status" >&2
    exit 2
    ;;
esac
