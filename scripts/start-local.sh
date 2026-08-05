#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

docker compose up -d postgres redis

echo "Waiting for PostgreSQL..."
until docker compose exec -T postgres pg_isready -U nearhome -d nearhome >/dev/null 2>&1; do
  sleep 1
done

cd apps/api
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -e ".[dev]"
alembic upgrade head
python -m app.db.seed_demo || true
cd "$ROOT"

echo ""
echo "NearHome local stack ready."
echo "  API:      cd apps/api && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000"
echo "  Web:      cd apps/web && npm run dev"
echo "  Worker:   cd apps/api && source .venv/bin/activate && python -m app.jobs.worker"
echo ""
echo "Or run: docker compose up --build"
