#!/bin/sh
set -eu

# Render and similar hosts provide PORT at runtime. Local Docker retains 8000.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
