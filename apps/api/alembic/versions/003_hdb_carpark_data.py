"""Add official HDB carpark and parking-score persistence."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_hdb_carpark_data"
down_revision: Union[str, None] = "002_retire_storey_cost"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hdb_carparks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("carpark_no", sa.String(40), nullable=False, unique=True),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("carpark_type", sa.String(80)),
        sa.Column("source_carpark_type", sa.String(120)),
        sa.Column("parking_system_type", sa.String(80)),
        sa.Column("short_term_parking", sa.String(120)),
        sa.Column("free_parking", sa.String(120)),
        sa.Column("night_parking", sa.String(120)),
        sa.Column("carpark_decks", sa.Integer()),
        sa.Column("gantry_height_m", sa.Float()),
        sa.Column("basement_indicator", sa.String(20)),
        sa.Column("source", sa.String(120), nullable=False, server_default="data.gov.sg"),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_hdb_carparks_geo", "hdb_carparks", ["latitude", "longitude"])
    op.create_index("ix_hdb_carparks_number", "hdb_carparks", ["carpark_no"])
    op.create_table(
        "carpark_availability_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("carpark_no", sa.String(40), nullable=False),
        sa.Column("lot_type", sa.String(10), nullable=False),
        sa.Column("total_lots", sa.Integer()),
        sa.Column("available_lots", sa.Integer()),
        sa.Column("availability_pct", sa.Float()),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.UniqueConstraint("carpark_no", "lot_type", "observed_at", name="uq_carpark_availability_snapshot"),
    )
    op.create_index("ix_carpark_availability_number_time", "carpark_availability_snapshots", ["carpark_no", "observed_at"])
    op.create_table(
        "listing_carpark_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("confirmed_listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("carpark_no", sa.String(40), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("haversine_distance_m", sa.Float(), nullable=False),
        sa.Column("routed_walk_distance_m", sa.Float()),
        sa.Column("routed_walk_minutes", sa.Float()),
        sa.Column("relevance_score", sa.Float()),
        sa.Column("match_type", sa.String(40), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("listing_id", "carpark_no", name="uq_listing_carpark_match"),
    )
    op.create_index("ix_listing_carpark_matches_listing", "listing_carpark_matches", ["listing_id"])
    op.create_table(
        "parking_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("confirmed_listings.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("score", sa.Float()),
        sa.Column("score_status", sa.String(40), nullable=False),
        sa.Column("metric_json", postgresql.JSONB(), nullable=False),
        sa.Column("score_version", sa.String(40), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_parking_metrics_listing", "parking_metrics", ["listing_id"])


def downgrade() -> None:
    op.drop_table("parking_metrics")
    op.drop_table("listing_carpark_matches")
    op.drop_table("carpark_availability_snapshots")
    op.drop_table("hdb_carparks")
