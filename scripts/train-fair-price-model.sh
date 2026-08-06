#!/usr/bin/env bash
# Build the local CatBoost fair-price artifact outside API and worker requests.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$repo_root/apps/api/.venv/bin/python"

if [[ ! -x "$python_bin" ]]; then
  echo "NearHome API virtual environment is missing: $python_bin" >&2
  echo "Create it with: cd apps/api && python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'" >&2
  exit 1
fi

cd "$repo_root/apps/api"
exec "$python_bin" -m app.engines.fair_price_catboost train \
  --artifact-dir "$repo_root/artifacts/fair_price/catboost"
