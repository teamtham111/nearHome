#!/usr/bin/env bash
# Run Alembic exactly once against the configured Supabase PostgreSQL database.
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
DATABASE_URL_SECRET_NAME="${DATABASE_URL_SECRET_NAME:-nearhome-database-url}"

command -v gcloud >/dev/null 2>&1 || { echo "Missing required command: gcloud" >&2; exit 1; }
command -v alembic >/dev/null 2>&1 || { echo "Missing required command: alembic (activate apps/api/.venv first)" >&2; exit 1; }

export DATABASE_URL
DATABASE_URL="$(gcloud secrets versions access latest --project "$GOOGLE_CLOUD_PROJECT" --secret "$DATABASE_URL_SECRET_NAME")"

cd "$(dirname "$0")/../apps/api"
exec alembic upgrade head
