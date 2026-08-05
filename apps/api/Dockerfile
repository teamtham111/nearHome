FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY apps/api/pyproject.toml apps/api/
COPY apps/api/app apps/api/app
COPY apps/api/alembic apps/api/alembic
COPY apps/api/alembic.ini apps/api/
# Enrichment uses curated, repository-managed fixtures at runtime. Keep these
# in the immutable image so production never silently loses evidence data.
COPY data_pipeline/fixtures data_pipeline/fixtures
COPY scripts/start-api.sh scripts/start-api.sh

WORKDIR /app/apps/api

RUN pip install --no-cache-dir -e .
# Smart Paste's headless-browser fallback (for pages that block plain HTTP fetches).
RUN playwright install --with-deps chromium
RUN chmod +x /app/scripts/start-api.sh

EXPOSE 8000
CMD ["/app/scripts/start-api.sh"]
