#!/usr/bin/env bash
# Provision the reversible, dedicated Direct VPC + Cloud NAT experiment.
set -euo pipefail

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${2:-${CLOUD_RUN_REGION:-asia-southeast1}}"
SERVICE_NAME="${3:-${CLOUD_RUN_SERVICE_NAME:-nearhome-api}}"
NETWORK="${STATIC_EGRESS_NETWORK:-nearhome-egress-vpc}"
SUBNET="${STATIC_EGRESS_SUBNET:-nearhome-egress-subnet}"
ROUTER="${STATIC_EGRESS_ROUTER:-nearhome-egress-router}"
NAT="${STATIC_EGRESS_NAT:-nearhome-egress-nat}"
ADDRESS="${STATIC_EGRESS_ADDRESS:-nearhome-egress-ip}"
CIDR="${STATIC_EGRESS_CIDR:-10.250.0.0/26}"

: "${PROJECT_ID:?Pass project ID as argument 1 or set GOOGLE_CLOUD_PROJECT}"
command -v gcloud >/dev/null || { echo "gcloud is required" >&2; exit 1; }

cidr_to_range() {
  local cidr="$1" ip prefix a b c d mask start end
  IFS=/ read -r ip prefix <<<"$cidr"; IFS=. read -r a b c d <<<"$ip"
  [[ "$prefix" =~ ^[0-9]+$ ]] && ((prefix >= 0 && prefix <= 32)) || return 1
  mask=$(( 0xFFFFFFFF << (32-prefix) & 0xFFFFFFFF ))
  start=$(( ((a<<24)|(b<<16)|(c<<8)|d) & mask ))
  end=$(( start | (0xFFFFFFFF ^ mask) ))
  printf '%u %u\n' "$start" "$end"
}

gcloud services enable compute.googleapis.com --project "$PROJECT_ID" --quiet
gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" --region "$REGION" >/dev/null || {
  echo "Cloud Run service $SERVICE_NAME was not found in $REGION" >&2; exit 1;
}

read -r wanted_start wanted_end < <(cidr_to_range "$CIDR") || { echo "Invalid CIDR: $CIDR" >&2; exit 1; }
while IFS= read -r existing; do
  [[ -z "$existing" ]] && continue
  # A matching subnet created by an earlier run is the intended idempotent case.
  if [[ "$existing" == "$CIDR" ]] && gcloud compute networks subnets describe "$SUBNET" --project "$PROJECT_ID" --region "$REGION" >/dev/null 2>&1; then
    continue
  fi
  read -r existing_start existing_end < <(cidr_to_range "$existing") || continue
  if (( wanted_start <= existing_end && existing_start <= wanted_end )); then
    echo "Requested subnet $CIDR overlaps existing subnet $existing; choose STATIC_EGRESS_CIDR." >&2
    exit 1
  fi
done < <(gcloud compute networks subnets list --project "$PROJECT_ID" --format='value(ipCidrRange)')

echo "Configuring static egress for $SERVICE_NAME in $PROJECT_ID/$REGION"
echo "  VPC=$NETWORK subnet=$SUBNET ($CIDR) router=$ROUTER NAT=$NAT address=$ADDRESS"

if gcloud compute networks describe "$NETWORK" --project "$PROJECT_ID" >/dev/null 2>&1; then
  mode="$(gcloud compute networks describe "$NETWORK" --project "$PROJECT_ID" --format='value(autoCreateSubnetworks)')"
  [[ "$mode" == "False" ]] || { echo "Existing $NETWORK is not custom-mode; refusing to reuse it." >&2; exit 1; }
else
  gcloud compute networks create "$NETWORK" --project "$PROJECT_ID" --subnet-mode=custom
fi

if gcloud compute networks subnets describe "$SUBNET" --project "$PROJECT_ID" --region "$REGION" >/dev/null 2>&1; then
  actual_network="$(gcloud compute networks subnets describe "$SUBNET" --project "$PROJECT_ID" --region "$REGION" --format='value(network)')"
  actual_cidr="$(gcloud compute networks subnets describe "$SUBNET" --project "$PROJECT_ID" --region "$REGION" --format='value(ipCidrRange)')"
  [[ "$actual_network" == */"$NETWORK" && "$actual_cidr" == "$CIDR" ]] || { echo "Existing subnet differs from requested configuration; refusing to change it." >&2; exit 1; }
else
  gcloud compute networks subnets create "$SUBNET" --project "$PROJECT_ID" --network "$NETWORK" --region "$REGION" --range "$CIDR"
fi

if ! gcloud compute routers describe "$ROUTER" --project "$PROJECT_ID" --region "$REGION" >/dev/null 2>&1; then
  gcloud compute routers create "$ROUTER" --project "$PROJECT_ID" --network "$NETWORK" --region "$REGION"
fi

if ! gcloud compute addresses describe "$ADDRESS" --project "$PROJECT_ID" --region "$REGION" >/dev/null 2>&1; then
  gcloud compute addresses create "$ADDRESS" --project "$PROJECT_ID" --region "$REGION"
fi

if ! gcloud compute routers nats describe "$NAT" --project "$PROJECT_ID" --router "$ROUTER" --region "$REGION" >/dev/null 2>&1; then
  gcloud compute routers nats create "$NAT" --project "$PROJECT_ID" --router "$ROUTER" --region "$REGION" \
    --nat-custom-subnet-ip-ranges "$SUBNET" --nat-external-ip-pool "$ADDRESS" --enable-logging --log-filter ERRORS_ONLY
fi

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:service-$PROJECT_NUMBER@serverless-robot-prod.iam.gserviceaccount.com" \
  --role=roles/compute.networkUser --quiet >/dev/null

echo "Reserved static IP: $(gcloud compute addresses describe "$ADDRESS" --project "$PROJECT_ID" --region "$REGION" --format='value(address)')"
echo "Infrastructure is ready. This script does not change Cloud Run networking or traffic."
