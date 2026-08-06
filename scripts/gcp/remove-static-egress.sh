#!/usr/bin/env bash
# Explicit-only cleanup for the static-egress experiment. Never runs automatically.
set -euo pipefail

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${2:-${CLOUD_RUN_REGION:-asia-southeast1}}"
NETWORK="${STATIC_EGRESS_NETWORK:-nearhome-egress-vpc}"
SUBNET="${STATIC_EGRESS_SUBNET:-nearhome-egress-subnet}"
ROUTER="${STATIC_EGRESS_ROUTER:-nearhome-egress-router}"
NAT="${STATIC_EGRESS_NAT:-nearhome-egress-nat}"
ADDRESS="${STATIC_EGRESS_ADDRESS:-nearhome-egress-ip}"
: "${PROJECT_ID:?Pass project ID as argument 1 or set GOOGLE_CLOUD_PROJECT}"

[[ "${CONFIRM_REMOVE_STATIC_EGRESS:-}" == "DELETE_STATIC_EGRESS" ]] || {
  echo "Refusing cleanup. Re-run with CONFIRM_REMOVE_STATIC_EGRESS=DELETE_STATIC_EGRESS." >&2; exit 2;
}
command -v gcloud >/dev/null || { echo "gcloud is required" >&2; exit 1; }

echo "Deleting NAT, router, subnet, VPC, then reserved IP. Cloud Run must be detached first."
gcloud compute routers nats delete "$NAT" --project "$PROJECT_ID" --router "$ROUTER" --region "$REGION" --quiet 2>/dev/null || true
gcloud compute routers delete "$ROUTER" --project "$PROJECT_ID" --region "$REGION" --quiet 2>/dev/null || true
gcloud compute networks subnets delete "$SUBNET" --project "$PROJECT_ID" --region "$REGION" --quiet 2>/dev/null || true
gcloud compute networks delete "$NETWORK" --project "$PROJECT_ID" --quiet 2>/dev/null || true
gcloud compute addresses delete "$ADDRESS" --project "$PROJECT_ID" --region "$REGION" --quiet 2>/dev/null || true
