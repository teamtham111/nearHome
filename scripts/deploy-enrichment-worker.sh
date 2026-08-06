#!/usr/bin/env bash
# Deploy the private Cloud Run target used only by Google Cloud Tasks.
set -euo pipefail

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_command gcloud

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${CLOUD_RUN_REGION:-asia-southeast1}"
WORKER_SERVICE_NAME="${ENRICHMENT_WORKER_SERVICE_NAME:-nearhome-enrichment-worker}"
ARTIFACT_REPOSITORY="${ARTIFACT_REGISTRY_REPOSITORY:-nearhome-api}"
SERVICE_ACCOUNT="${ENRICHMENT_WORKER_SERVICE_ACCOUNT:-}"
TASK_SERVICE_ACCOUNT="${CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL:-}"
TASK_QUEUE="${CLOUD_TASKS_QUEUE:-nearhome-enrichment}"
TASK_LOCATION="${CLOUD_TASKS_LOCATION:-$REGION}"

: "${PROJECT_ID:?Pass a Google Cloud project ID as the first argument or set GOOGLE_CLOUD_PROJECT}"
: "${SERVICE_ACCOUNT:?Set ENRICHMENT_WORKER_SERVICE_ACCOUNT to the worker runtime service-account email}"
: "${TASK_SERVICE_ACCOUNT:?Set CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL to the worker-invoker service account}"
: "${WEB_URL:?Set WEB_URL to the exact production Vercel HTTPS origin}"
: "${CORS_ORIGINS:?Set CORS_ORIGINS to one or more exact Vercel HTTPS origins}"

DATABASE_URL_SECRET_NAME="${DATABASE_URL_SECRET_NAME:-nearhome-database-url}"
SECRET_KEY_SECRET_NAME="${SECRET_KEY_SECRET_NAME:-nearhome-secret-key}"
GOOGLE_MAPS_API_KEY_SECRET_NAME="${GOOGLE_MAPS_API_KEY_SECRET_NAME:-nearhome-google-maps-api-key}"
ONEMAP_EMAIL_SECRET_NAME="${ONEMAP_EMAIL_SECRET_NAME:-nearhome-onemap-email}"
ONEMAP_PASSWORD_SECRET_NAME="${ONEMAP_PASSWORD_SECRET_NAME:-nearhome-onemap-password}"
GROQ_API_KEY_SECRET_NAME="${GROQ_API_KEY_SECRET_NAME:-nearhome-groq-api-key}"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable run.googleapis.com cloudtasks.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com

if ! gcloud artifacts repositories describe "$ARTIFACT_REPOSITORY" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$ARTIFACT_REPOSITORY" \
    --repository-format=docker \
    --location "$REGION" \
    --description="NearHome Cloud Run images"
fi

for secret_name in "$DATABASE_URL_SECRET_NAME" "$SECRET_KEY_SECRET_NAME" "$GOOGLE_MAPS_API_KEY_SECRET_NAME" "$ONEMAP_EMAIL_SECRET_NAME" "$ONEMAP_PASSWORD_SECRET_NAME" "$GROQ_API_KEY_SECRET_NAME"; do
  gcloud secrets describe "$secret_name" >/dev/null
  gcloud secrets add-iam-policy-binding "$secret_name" \
    --member="serviceAccount:$SERVICE_ACCOUNT" \
    --role="roles/secretmanager.secretAccessor" --quiet >/dev/null
done

IMAGE="${ENRICHMENT_WORKER_IMAGE:-$REGION-docker.pkg.dev/$PROJECT_ID/$ARTIFACT_REPOSITORY/nearhome-api:$(git rev-parse --short HEAD)}"
if [[ -z "${ENRICHMENT_WORKER_IMAGE:-}" ]]; then
  gcloud builds submit . --config cloudbuild.yaml --substitutions="_IMAGE=$IMAGE"
fi
SECRET_MAPPINGS="DATABASE_URL=$DATABASE_URL_SECRET_NAME:latest,SECRET_KEY=$SECRET_KEY_SECRET_NAME:latest,GOOGLE_MAPS_API_KEY=$GOOGLE_MAPS_API_KEY_SECRET_NAME:latest,ONEMAP_EMAIL=$ONEMAP_EMAIL_SECRET_NAME:latest,ONEMAP_PASSWORD=$ONEMAP_PASSWORD_SECRET_NAME:latest,GROQ_API_KEY=$GROQ_API_KEY_SECRET_NAME:latest"
ENV_VARS="^|^APP_ENV=production|DEMO_MODE=false|LOG_LEVEL=INFO|JOB_EXECUTION_MODE=cloud_tasks|GCP_PROJECT_ID=$PROJECT_ID|CLOUD_TASKS_LOCATION=$TASK_LOCATION|CLOUD_TASKS_QUEUE=$TASK_QUEUE|ENRICHMENT_WORKER_URL=https://placeholder.invalid|CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL=$TASK_SERVICE_ACCOUNT|CLOUD_TASKS_OIDC_AUDIENCE=https://placeholder.invalid|CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS=600|MAX_ENRICHMENT_JOB_ATTEMPTS=3|ENRICHMENT_JOB_STALE_SECONDS=660|MAX_CONCURRENT_ENRICHMENTS=1|ENABLE_PLAYWRIGHT_FALLBACK=true|PLAYWRIGHT_TIMEOUT_SECONDS=25|PLAYWRIGHT_MAX_CONCURRENCY=1|DATABASE_POOL_SIZE=3|DATABASE_MAX_OVERFLOW=2|DATABASE_POOL_RECYCLE_SECONDS=300|WEB_URL=$WEB_URL|CORS_ORIGINS=$CORS_ORIGINS"

gcloud run deploy "$WORKER_SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account "$SERVICE_ACCOUNT" \
  --no-allow-unauthenticated \
  --execution-environment gen2 \
  --min-instances 0 \
  --max-instances "${ENRICHMENT_WORKER_MAX_INSTANCES:-1}" \
  --cpu 1 \
  --memory 2Gi \
  --concurrency 1 \
  --timeout 600 \
  --no-cpu-throttling \
  --command uvicorn \
  --args app.worker_main:worker_app,--host=0.0.0.0,--port=8080,--workers=1 \
  --set-env-vars "$ENV_VARS" \
  --set-secrets "$SECRET_MAPPINGS"

gcloud run services add-iam-policy-binding "$WORKER_SERVICE_NAME" \
  --region "$REGION" \
  --member="serviceAccount:$TASK_SERVICE_ACCOUNT" \
  --role="roles/run.invoker" \
  --quiet >/dev/null

WORKER_URL="$(gcloud run services describe "$WORKER_SERVICE_NAME" --region "$REGION" --format='value(status.url)')"
echo "Private enrichment worker deployed: $WORKER_URL"
echo "Use this URL as ENRICHMENT_WORKER_URL when deploying nearhome-api."
