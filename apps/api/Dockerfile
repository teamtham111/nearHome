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
# Build the immutable fair-price artifact once. Runtime inference only loads
# this file and falls back to comparable evidence if it is incompatible.
RUN mkdir -p /app/model-artifacts/fair-price && \
    FAIR_PRICE_MODEL_ARTIFACT_PATH=/app/model-artifacts/fair-price \
    python -m app.engines.fair_price_catboost train --artifact-dir /app/model-artifacts/fair-price
ENV FAIR_PRICE_MODEL_ARTIFACT_PATH=/app/model-artifacts/fair-price
# Smart Paste's headless-browser fallback (for pages that block plain HTTP fetches).
RUN playwright install --with-deps chromium
RUN chmod +x /app/scripts/start-api.sh
ENV TMPDIR=/tmp

EXPOSE 8080
CMD ["/app/scripts/start-api.sh"]
