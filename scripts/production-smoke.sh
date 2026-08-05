#!/bin/sh
# Read-only production smoke checks. It never creates sessions or calls paid providers.
set -eu

: "${FRONTEND_URL:?Set FRONTEND_URL, for example https://app.example.com}"
: "${BACKEND_URL:?Set BACKEND_URL, for example https://api.example.com}"

trimmed_frontend_url=${FRONTEND_URL%/}
trimmed_backend_url=${BACKEND_URL%/}

echo "Checking frontend"
curl --fail --silent --show-error --max-time 15 "$trimmed_frontend_url/" >/dev/null

echo "Checking API process"
curl --fail --silent --show-error --max-time 10 "$trimmed_backend_url/api/v1/health" >/dev/null

echo "Checking API readiness"
curl --fail --silent --show-error --max-time 10 "$trimmed_backend_url/api/v1/ready" >/dev/null

echo "Checking CORS preflight"
headers_file=$(mktemp)
trap 'rm -f "$headers_file"' EXIT
curl --fail --silent --show-error --max-time 10 -D "$headers_file" -o /dev/null -X OPTIONS \
  -H "Origin: $trimmed_frontend_url" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" \
  "$trimmed_backend_url/api/v1/sessions"
if ! grep -qi "^access-control-allow-origin: $trimmed_frontend_url" "$headers_file"; then
  echo "CORS preflight did not allow FRONTEND_URL" >&2
  exit 1
fi

echo "Production smoke checks passed. Run the browser checklist in docs/DEPLOYMENT.md next."
