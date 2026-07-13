#!/usr/bin/env bash
# Prove the protected WIF identity can approve/read builds and cannot mutate release inputs or IAM.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-customer-news-475010}"
BUILD_SERVICE_ACCOUNT="${BUILD_SERVICE_ACCOUNT:-cn-cicd-sa@customer-news-475010.iam.gserviceaccount.com}"
SOURCE_BUCKET="${SOURCE_BUCKET:-customer-news-475010_cloudbuild}"
PROBE_TRIGGER_ID="${PROBE_TRIGGER_ID:-03928806-ecbf-45e5-aa92-5fb64d430060}"

command -v curl >/dev/null 2>&1 || {
  echo "ERROR: curl is required." >&2
  exit 1
}
command -v jq >/dev/null 2>&1 || {
  echo "ERROR: jq is required." >&2
  exit 1
}

# Cloud Build cancellation is gated by cloudbuild.builds.update. Manual
# triggers.run is gated by cloudbuild.builds.create; neither permission is held.
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
# The exact direct-role read-back separately proves that none of these trigger
# permissions is granted: cloudbuild.triggers.create,
# cloudbuild.triggers.delete, cloudbuild.triggers.update. A read-only describe
# below must also be denied to the live federated identity.
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

if gcloud builds triggers describe "${PROBE_TRIGGER_ID}" \
  --project="${PROJECT_ID}" \
  --region=europe-west1 >/dev/null 2>&1; then
  echo "ERROR: release approver unexpectedly has Cloud Build trigger read access." >&2
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
