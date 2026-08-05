#!/bin/sh
set -eu

# Cloud Run injects PORT. Default to its standard port for local containers.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1 --proxy-headers --forwarded-allow-ips="*"
