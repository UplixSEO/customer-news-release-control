#!/usr/bin/env bash
# Read-only classifier for the Customer News production gate and release ledger.
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: diagnose_release_gate.sh <run-id> [--job <name>]
       diagnose_release_gate.sh --run <run-id> [--job <name>]
       diagnose_release_gate.sh --deployment <id> [--reference-instant <iso8601>]
       diagnose_release_gate.sh --fixture <json>
EOF
}

mode=""
run_id=""
deployment_id=""
fixture=""
reference_instant=""
job_override=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run)
      mode="run"
      run_id="${2:-}"
      shift 2
      ;;
    --deployment)
      mode="deployment"
      deployment_id="${2:-}"
      shift 2
      ;;
    --fixture)
      mode="fixture"
      fixture="${2:-}"
      shift 2
      ;;
    --reference-instant)
      reference_instant="${2:-}"
      shift 2
      ;;
    --job)
      job_override="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "${mode}" && "$1" =~ ^[0-9]+$ ]]; then
        mode="run"
        run_id="$1"
        shift
      else
        echo "ERROR: unknown argument: $1" >&2
        usage >&2
        exit 2
      fi
      ;;
  esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${mode}" == "fixture" ]]; then
  [[ -r "${fixture}" ]] || {
    echo "ERROR: fixture is not readable: ${fixture}" >&2
    exit 2
  }
  exec python3 "${script_dir}/release_gate_state.py" <"${fixture}"
fi

reference_instant="${reference_instant:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

repository="${GITHUB_REPOSITORY:-}"
if [[ -z "${repository}" ]]; then
  remote="$(git config --get remote.origin.url 2>/dev/null || true)"
  remote="${remote%.git}"
  if [[ "${remote}" =~ github\.com[:/](.+)$ ]]; then
    repository="${BASH_REMATCH[1]}"
  fi
fi
repository="${repository:-UplixSEO/customer-news-release-control}"

api_errors=()
api_out=""
api_get() {
  local url="$1"
  local fallback="$2"
  if api_out="$(gh api "${url}" 2>/dev/null)"; then
    return 0
  fi
  api_errors+=("${url}")
  api_out="${fallback}"
}

api_errors_json() {
  if [[ ${#api_errors[@]} -eq 0 ]]; then
    printf '[]'
    return
  fi
  printf '%s\n' "${api_errors[@]}" | jq -R . | jq -s .
}

if [[ "${mode}" == "deployment" ]]; then
  [[ "${deployment_id}" =~ ^[0-9]+$ ]] || {
    echo "ERROR: --deployment requires a numeric deployment id" >&2
    exit 2
  }
  api_get "repos/${repository}/deployments/${deployment_id}" "null"
  deployment_json="${api_out}"
  if [[ "${deployment_json}" != "null" ]]; then
    api_get "repos/${repository}/deployments/${deployment_id}/statuses?per_page=100" "[]"
    deployment_json="$(jq -c --argjson statuses "${api_out}" '. + {statuses: $statuses}' <<<"${deployment_json}")"
  fi
  payload="$(jq -n \
    --arg mode "deployment" \
    --arg reference_instant "${reference_instant}" \
    --argjson deployment "${deployment_json}" \
    --argjson api_errors "$(api_errors_json)" \
    '{mode: $mode, reference_instant: $reference_instant, deployment: $deployment, api_errors: $api_errors}')"
  printf '%s' "${payload}" | python3 "${script_dir}/release_gate_state.py"
  exit $?
fi

[[ "${run_id}" =~ ^[0-9]+$ ]] || {
  echo "ERROR: a numeric run id is required" >&2
  exit 2
}

api_get "repos/${repository}/actions/runs/${run_id}" "null"
run_json="${api_out}"
api_get "repos/${repository}/actions/runs/${run_id}/jobs?per_page=100" "{\"jobs\":[]}"
jobs_json="${api_out}"
api_get "repos/${repository}/environments/production" "null"
environment_json="${api_out}"
api_get "repos/${repository}/actions/runs/${run_id}/pending_deployments" "[]"
pending_json="${api_out}"

wait_timer_minutes="$(jq -r '[(.protection_rules // [])[] | select(.type == "wait_timer") | .wait_timer][0] // 15' <<<"${environment_json}" 2>/dev/null || true)"
wait_timer_minutes="${wait_timer_minutes:-15}"

job_name="${job_override}"
if [[ -z "${job_name}" ]]; then
  case "$(jq -r '.path // ""' <<<"${run_json}" 2>/dev/null || true)" in
    .github/workflows/promote.yml) job_name="Validate and approve fixed release batch" ;;
    .github/workflows/auto-promote.yml) job_name="Detect promote candidate and dispatch" ;;
  esac
fi
job_json="$(jq -c --arg name "${job_name}" '[.jobs[] | select(.name == $name)][0] // null' <<<"${jobs_json}" 2>/dev/null || true)"
job_json="${job_json:-null}"

head_sha="$(jq -r '.head_sha // ""' <<<"${run_json}" 2>/dev/null || true)"
deployments_url="repos/${repository}/deployments?environment=production&per_page=100"
if [[ -n "${head_sha}" ]]; then
  deployments_url="${deployments_url}&sha=${head_sha}"
fi
api_get "${deployments_url}" "[]"
run_deployments_json="${api_out}"

job_created="$(jq -r '.created_at // ""' <<<"${job_json}" 2>/dev/null || true)"
deployments="[]"
if [[ -n "${job_created}" ]]; then
  while IFS= read -r deployment; do
    [[ -n "${deployment}" ]] || continue
    candidate_id="$(jq -r '.id' <<<"${deployment}")"
    api_get "repos/${repository}/deployments/${candidate_id}/statuses?per_page=100" "[]"
    deployments="$(jq -c --argjson deployment "${deployment}" --argjson statuses "${api_out}" \
      '. + [$deployment + {statuses: $statuses}]' <<<"${deployments}")"
  done < <(jq -c --arg created "${job_created}" '
    .[]
    | select(.created_at != null)
    | select(((.created_at | fromdateiso8601) - ($created | fromdateiso8601)) | fabs <= 5)
  ' <<<"${run_deployments_json}" 2>/dev/null || true)
fi

payload="$(jq -n \
  --arg mode "run" \
  --arg reference_instant "${reference_instant}" \
  --argjson wait_timer_minutes "${wait_timer_minutes}" \
  --argjson grace_seconds 120 \
  --argjson run "${run_json}" \
  --argjson job "${job_json}" \
  --argjson deployments "${deployments}" \
  --argjson pending_deployments "${pending_json}" \
  --argjson api_errors "$(api_errors_json)" \
  '{mode: $mode, reference_instant: $reference_instant, wait_timer_minutes: $wait_timer_minutes,
    grace_seconds: $grace_seconds, run: $run, job: $job, deployments: $deployments,
    pending_deployments: $pending_deployments, api_errors: $api_errors}')"

printf '%s' "${payload}" | python3 "${script_dir}/release_gate_state.py"
exit $?
