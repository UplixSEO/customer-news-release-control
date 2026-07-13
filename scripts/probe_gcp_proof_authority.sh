#!/usr/bin/env bash
# Prove the public runtime identity has read surfaces and no mutation authority.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-customer-news-475010}"
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-customer-news-475010-cloudbuild-artifacts}"
BUILD_SERVICE_ACCOUNT="${BUILD_SERVICE_ACCOUNT:-cn-cicd-sa@customer-news-475010.iam.gserviceaccount.com}"

token="$(gcloud auth print-access-token)"
permissions='{
  "permissions": [
    "bigquery.tables.get", "bigquery.tables.getData", "bigquery.tables.updateData",
    "cloudbuild.builds.get", "cloudbuild.builds.list", "cloudbuild.builds.approve",
    "cloudbuild.builds.create", "cloudbuild.builds.update",
    "cloudfunctions.functions.get", "cloudfunctions.functions.update",
    "cloudscheduler.jobs.get", "cloudscheduler.jobs.update",
    "resourcemanager.projects.get", "resourcemanager.projects.setIamPolicy",
    "run.jobs.get", "run.jobs.update", "run.services.get", "run.services.update",
    "serviceusage.services.use"
  ]
}'
response="$(curl --fail --silent --show-error -X POST \
  -H "Authorization: Bearer ${token}" -H 'Content-Type: application/json' \
  --data "${permissions}" \
  "https://cloudresourcemanager.googleapis.com/v1/projects/${PROJECT_ID}:testIamPermissions")"
actual="$(jq -c '(.permissions // []) | sort' <<<"${response}")"
for required in \
  bigquery.tables.get bigquery.tables.getData \
  cloudbuild.builds.get cloudbuild.builds.list \
  cloudfunctions.functions.get cloudscheduler.jobs.get \
  resourcemanager.projects.get run.jobs.get run.services.get \
  serviceusage.services.use; do
  jq -e --arg permission "${required}" 'index($permission) != null' \
    <<<"${actual}" >/dev/null || {
      echo "ERROR: proof identity is missing ${required}." >&2
      exit 1
    }
done
for forbidden in \
  bigquery.tables.updateData cloudbuild.builds.approve cloudbuild.builds.create \
  cloudbuild.builds.update cloudfunctions.functions.update cloudscheduler.jobs.update \
  resourcemanager.projects.setIamPolicy run.jobs.update run.services.update; do
  jq -e --arg permission "${forbidden}" 'index($permission) == null' \
    <<<"${actual}" >/dev/null || {
      echo "ERROR: proof identity unexpectedly has ${forbidden}." >&2
      exit 1
    }
done

service_account_response="$(curl --fail --silent --show-error -X POST \
  -H "Authorization: Bearer ${token}" -H 'Content-Type: application/json' \
  --data '{"permissions":["iam.serviceAccounts.actAs"]}' \
  "https://iam.googleapis.com/v1/projects/-/serviceAccounts/${BUILD_SERVICE_ACCOUNT}:testIamPermissions")"
[[ "$(jq -c '(.permissions // []) | sort' <<<"${service_account_response}")" == '[]' ]]

storage_response="$(curl --fail --silent --show-error \
  -H "Authorization: Bearer ${token}" \
  "https://storage.googleapis.com/storage/v1/b/${ARTIFACT_BUCKET}/iam/testPermissions?permissions=storage.objects.get&permissions=storage.objects.list&permissions=storage.objects.create")"
storage_permissions="$(jq -c '(.permissions // []) | sort' <<<"${storage_response}")"
[[ "${storage_permissions}" == '["storage.objects.get","storage.objects.list"]' ]] || {
  echo "ERROR: proof bucket permissions drifted: ${storage_permissions}." >&2
  exit 1
}

echo "GCP proof boundary passed: runtime/build/BQ reads allowed; approval/deploy/IAM/actAs/write denied."
