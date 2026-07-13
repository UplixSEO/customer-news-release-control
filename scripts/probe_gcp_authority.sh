#!/usr/bin/env bash
# Prove the protected WIF identity can approve/read builds and cannot mutate release inputs or IAM.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-customer-news-475010}"
BUILD_SERVICE_ACCOUNT="${BUILD_SERVICE_ACCOUNT:-cn-cicd-sa@customer-news-475010.iam.gserviceaccount.com}"
SOURCE_BUCKET="${SOURCE_BUCKET:-customer-news-475010_cloudbuild}"

command -v curl >/dev/null 2>&1 || {
  echo "ERROR: curl is required." >&2
  exit 1
}
command -v jq >/dev/null 2>&1 || {
  echo "ERROR: jq is required." >&2
  exit 1
}

# Cloud Build cancellation is gated by cloudbuild.builds.update. Native
# triggers.create/patch/delete/run are gated by cloudbuild.builds.create;
# neither permission is held. triggers.run is gated by cloudbuild.builds.create.
project_permissions=(
  cloudbuild.builds.approve
  cloudbuild.builds.get
  cloudbuild.builds.list
  resourcemanager.projects.get
  serviceusage.services.use
  cloudbuild.builds.create
  cloudbuild.builds.update
  resourcemanager.projects.setIamPolicy
)
expected_project_permissions='["cloudbuild.builds.approve","cloudbuild.builds.get","cloudbuild.builds.list","resourcemanager.projects.get","serviceusage.services.use"]'

permissions_json="$(printf '%s\n' "${project_permissions[@]}" | jq -R . | jq -cs '{permissions: .}')"
access_token="$(gcloud auth print-access-token)"
project_response="$(curl --fail --silent --show-error \
  -X POST \
  -H "Authorization: Bearer ${access_token}" \
  -H 'Content-Type: application/json' \
  --data "${permissions_json}" \
  "https://cloudresourcemanager.googleapis.com/v1/projects/${PROJECT_ID}:testIamPermissions")"
actual_project_permissions="$(jq -c '(.permissions // []) | sort' <<<"${project_response}")"
if [[ "${actual_project_permissions}" != "${expected_project_permissions}" ]]; then
  echo "ERROR: effective project permissions drifted: ${actual_project_permissions}" >&2
  exit 1
fi

service_account_response="$(curl --fail --silent --show-error \
  -X POST \
  -H "Authorization: Bearer ${access_token}" \
  -H 'Content-Type: application/json' \
  --data '{"permissions":["iam.serviceAccounts.actAs"]}' \
  "https://iam.googleapis.com/v1/projects/-/serviceAccounts/${BUILD_SERVICE_ACCOUNT}:testIamPermissions")"
if [[ "$(jq -c '(.permissions // []) | sort' <<<"${service_account_response}")" != '[]' ]]; then
  echo "ERROR: release approver unexpectedly has iam.serviceAccounts.actAs." >&2
  exit 1
fi

storage_response="$(curl --fail --silent --show-error \
  -H "Authorization: Bearer ${access_token}" \
  "https://storage.googleapis.com/storage/v1/b/${SOURCE_BUCKET}/iam/testPermissions?permissions=storage.objects.create")"
if [[ "$(jq -c '(.permissions // []) | sort' <<<"${storage_response}")" != '[]' ]]; then
  echo "ERROR: release approver unexpectedly has storage.objects.create." >&2
  exit 1
fi

echo "GCP boundary passed: approve/get/list allowed; create/cancel/trigger/IAM/actAs/upload denied."
