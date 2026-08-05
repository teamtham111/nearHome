#!/usr/bin/env bash
# Build and deploy the NearHome FastAPI image to Cloud Run without printing secrets.
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
SERVICE_NAME="${CLOUD_RUN_SERVICE_NAME:-nearhome-api}"
ARTIFACT_REPOSITORY="${ARTIFACT_REGISTRY_REPOSITORY:-nearhome-api}"
SERVICE_ACCOUNT="${CLOUD_RUN_SERVICE_ACCOUNT:-}"

: "${PROJECT_ID:?Pass a Google Cloud project ID as the first argument or set GOOGLE_CLOUD_PROJECT}"
: "${SERVICE_ACCOUNT:?Set CLOUD_RUN_SERVICE_ACCOUNT to the Cloud Run runtime service-account email}"
: "${WEB_URL:?Set WEB_URL to the exact production Vercel HTTPS origin}"
: "${CORS_ORIGINS:?Set CORS_ORIGINS to one or more exact Vercel HTTPS origins}"

DATABASE_URL_SECRET_NAME="${DATABASE_URL_SECRET_NAME:-nearhome-database-url}"
SECRET_KEY_SECRET_NAME="${SECRET_KEY_SECRET_NAME:-nearhome-secret-key}"
GOOGLE_MAPS_API_KEY_SECRET_NAME="${GOOGLE_MAPS_API_KEY_SECRET_NAME:-nearhome-google-maps-api-key}"
ONEMAP_EMAIL_SECRET_NAME="${ONEMAP_EMAIL_SECRET_NAME:-nearhome-onemap-email}"
ONEMAP_PASSWORD_SECRET_NAME="${ONEMAP_PASSWORD_SECRET_NAME:-nearhome-onemap-password}"
GROQ_API_KEY_SECRET_NAME="${GROQ_API_KEY_SECRET_NAME:-nearhome-groq-api-key}"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com

if ! gcloud artifacts repositories describe "$ARTIFACT_REPOSITORY" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$ARTIFACT_REPOSITORY" \
    --repository-format=docker \
    --location "$REGION" \
    --description="NearHome Cloud Run API images"
fi

required_secrets=(
  "$DATABASE_URL_SECRET_NAME"
  "$SECRET_KEY_SECRET_NAME"
  "$GOOGLE_MAPS_API_KEY_SECRET_NAME"
  "$ONEMAP_EMAIL_SECRET_NAME"
  "$ONEMAP_PASSWORD_SECRET_NAME"
  "$GROQ_API_KEY_SECRET_NAME"
)
for secret_name in "${required_secrets[@]}"; do
  gcloud secrets describe "$secret_name" >/dev/null
  gcloud secrets add-iam-policy-binding "$secret_name" \
    --member="serviceAccount:$SERVICE_ACCOUNT" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet >/dev/null
done

IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$ARTIFACT_REPOSITORY/$SERVICE_NAME:$(git rev-parse --short HEAD)"
gcloud builds submit --tag "$IMAGE" .

SECRET_MAPPINGS="DATABASE_URL=$DATABASE_URL_SECRET_NAME:latest,SECRET_KEY=$SECRET_KEY_SECRET_NAME:latest,GOOGLE_MAPS_API_KEY=$GOOGLE_MAPS_API_KEY_SECRET_NAME:latest,ONEMAP_EMAIL=$ONEMAP_EMAIL_SECRET_NAME:latest,ONEMAP_PASSWORD=$ONEMAP_PASSWORD_SECRET_NAME:latest,GROQ_API_KEY=$GROQ_API_KEY_SECRET_NAME:latest"
ENV_VARS="^@^APP_ENV=production@DEMO_MODE=false@LOG_LEVEL=INFO@JOB_EXECUTION_MODE=inline@MAX_CONCURRENT_ENRICHMENTS=1@ENABLE_PLAYWRIGHT_FALLBACK=true@PLAYWRIGHT_TIMEOUT_SECONDS=25@PLAYWRIGHT_MAX_CONCURRENCY=1@DATABASE_POOL_SIZE=3@DATABASE_MAX_OVERFLOW=2@DATABASE_POOL_RECYCLE_SECONDS=300@WEB_URL=$WEB_URL@CORS_ORIGINS=$CORS_ORIGINS"

gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account "$SERVICE_ACCOUNT" \
  --allow-unauthenticated \
  --execution-environment gen2 \
  --min-instances 0 \
  --max-instances 1 \
  --cpu 1 \
  --memory 2Gi \
  --concurrency 1 \
  --timeout 600 \
  --cpu-throttling \
  --set-env-vars "$ENV_VARS" \
  --set-secrets "$SECRET_MAPPINGS"

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format='value(status.url)')"
echo "NearHome API deployed: $SERVICE_URL"
echo "Set NEXT_PUBLIC_API_BASE_URL=$SERVICE_URL in Vercel, then set WEB_URL/CORS_ORIGINS to the final Vercel URL and redeploy."
