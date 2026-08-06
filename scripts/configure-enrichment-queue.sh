#!/usr/bin/env bash
# Create the bounded Cloud Tasks queue and minimum IAM bindings for NearHome.
set -euo pipefail

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${CLOUD_TASKS_LOCATION:-${CLOUD_RUN_REGION:-asia-southeast1}}"
QUEUE="${CLOUD_TASKS_QUEUE:-nearhome-enrichment}"
API_SERVICE_ACCOUNT="${CLOUD_RUN_SERVICE_ACCOUNT:-}"
TASK_SERVICE_ACCOUNT="${CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL:-}"

: "${PROJECT_ID:?Pass a Google Cloud project ID as the first argument or set GOOGLE_CLOUD_PROJECT}"
: "${API_SERVICE_ACCOUNT:?Set CLOUD_RUN_SERVICE_ACCOUNT to the public API runtime service-account email}"
: "${TASK_SERVICE_ACCOUNT:?Set CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL to the worker-invoker service account}"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable cloudtasks.googleapis.com iamcredentials.googleapis.com

if ! gcloud tasks queues describe "$QUEUE" --location "$REGION" >/dev/null 2>&1; then
  gcloud tasks queues create "$QUEUE" --location "$REGION"
fi
gcloud tasks queues update "$QUEUE" --location "$REGION" \
  --max-dispatches-per-second=1 \
  --max-concurrent-dispatches=1 \
  --max-attempts=3

gcloud tasks queues add-iam-policy-binding "$QUEUE" --location "$REGION" \
  --member="serviceAccount:$API_SERVICE_ACCOUNT" \
  --role="roles/cloudtasks.enqueuer" --quiet >/dev/null

# Creating an HTTP task with an OIDC token also requires the enqueueing API
# identity to be allowed to act as the delivery identity. Keep that permission
# scoped to this one service account rather than granting it project-wide.
gcloud iam service-accounts add-iam-policy-binding "$TASK_SERVICE_ACCOUNT" \
  --member="serviceAccount:$API_SERVICE_ACCOUNT" \
  --role="roles/iam.serviceAccountUser" --quiet >/dev/null

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud iam service-accounts add-iam-policy-binding "$TASK_SERVICE_ACCOUNT" \
  --member="serviceAccount:service-$PROJECT_NUMBER@gcp-sa-cloudtasks.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" --quiet >/dev/null

echo "Cloud Tasks queue configured: $QUEUE ($REGION)"
