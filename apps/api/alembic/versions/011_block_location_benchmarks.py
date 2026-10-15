"""Add HDB block registry, shared block features and location benchmarks.

Rollback: ``alembic downgrade 010_session_feedback`` drops only the new
tables and the nullable ``confirmed_listings.block_key`` column; existing
listing, enrichment and scoring data are untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "011_block_location_benchmarks"
down_revision: str | None = "010_session_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("confirmed_listings", sa.Column("block_key", sa.String(160), nullable=True))
    op.create_index("ix_confirmed_listings_block_key", "confirmed_listings", ["block_key"])

    op.create_table(
        "hdb_blocks",
        sa.Column("block_key", sa.String(160), primary_key=True),
        sa.Column("block", sa.String(20), nullable=False),
        sa.Column("street", sa.String(160), nullable=False),
        sa.Column("residential", sa.Boolean(), nullable=False),
        sa.Column("town_code", sa.String(10)),
        sa.Column("town_name", sa.String(80)),
        sa.Column("postal_code", sa.String(6)),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("geocode_status", sa.String(30)),
        sa.Column("geocode_match", sa.String(30)),
        sa.Column("onemap_road_name", sa.String(160)),
        sa.Column("year_completed", sa.Integer()),
        sa.Column("max_floor_level", sa.Integer()),
        sa.Column("total_dwelling_units", sa.Integer()),
        sa.Column("registry_version", sa.String(40), nullable=False),
        sa.Column("source_updated_at", sa.String(40)),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_hdb_blocks_postal_code", "hdb_blocks", ["postal_code"])
    op.create_index("ix_hdb_blocks_town_code", "hdb_blocks", ["town_code"])

    op.create_table(
        "block_location_features",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("block_key", sa.String(160), nullable=False),
        sa.Column("feature_set", sa.String(40), nullable=False),
        sa.Column("calculation_version", sa.String(80), nullable=False),
        sa.Column("source_fingerprint", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("origin_latitude", sa.Float(), nullable=False),
        sa.Column("origin_longitude", sa.Float(), nullable=False),
        sa.Column("origin_source", sa.String(40), nullable=False),
        sa.Column("measurements_json", postgresql.JSONB(), nullable=False),
        sa.Column("payload_gz", sa.LargeBinary()),
        sa.Column("provenance", sa.String(50)),
        sa.Column("computed_for", sa.String(20), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("block_key", "feature_set", "calculation_version", name="uq_block_feature_version"),
    )
    op.create_index(
        "ix_block_location_features_feature", "block_location_features", ["feature_set", "calculation_version"]
    )

    op.create_table(
        "location_benchmark_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("ranking_version", sa.String(40), nullable=False),
        sa.Column("measurement_version", sa.String(40), nullable=False),
        sa.Column("calculation_versions", postgresql.JSONB(), nullable=False),
        sa.Column("registry_version", sa.String(40)),
        sa.Column("registry_source_updated_at", sa.String(40)),
        sa.Column("summary_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_location_benchmark_versions_status", "location_benchmark_versions", ["status"])

    op.create_table(
        "location_benchmark_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("location_benchmark_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("block_key", sa.String(160), nullable=False),
        sa.Column("measurement_key", sa.String(60), nullable=False),
        sa.Column("value", sa.Float()),
        sa.Column("value_status", sa.String(20), nullable=False),
        sa.Column("town_code", sa.String(10)),
        sa.Column("national_json", postgresql.JSONB(), nullable=False),
        sa.Column("town_json", postgresql.JSONB(), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("version_id", "block_key", "measurement_key", name="uq_benchmark_result"),
    )
    op.create_index("ix_location_benchmark_results_block", "location_benchmark_results", ["version_id", "block_key"])

    op.create_table(
        "location_benchmark_cohorts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("location_benchmark_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("measurement_key", sa.String(60), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("cohort_json", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("version_id", "measurement_key", "scope", name="uq_benchmark_cohort"),
    )


def downgrade() -> None:
    op.drop_table("location_benchmark_cohorts")
    op.drop_table("location_benchmark_results")
    op.drop_index("ix_location_benchmark_versions_status", table_name="location_benchmark_versions")
    op.drop_table("location_benchmark_versions")
    op.drop_index("ix_block_location_features_feature", table_name="block_location_features")
    op.drop_table("block_location_features")
    op.drop_index("ix_hdb_blocks_town_code", table_name="hdb_blocks")
    op.drop_index("ix_hdb_blocks_postal_code", table_name="hdb_blocks")
    op.drop_table("hdb_blocks")
    op.drop_index("ix_confirmed_listings_block_key", table_name="confirmed_listings")
    op.drop_column("confirmed_listings", "block_key")
