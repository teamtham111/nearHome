"""Initial schema."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "comparison_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("demo_mode", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "buyer_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("comparison_sessions.id", ondelete="CASCADE"), unique=True),
        sa.Column("max_budget", sa.Float(), nullable=False),
        sa.Column("main_transport_mode", sa.String(50), nullable=False),
        sa.Column("schools_matter", sa.Boolean(), server_default="false"),
        sa.Column("named_school", sa.String(255)),
        sa.Column("immediate_costs_count_against_budget", sa.Boolean(), server_default="true"),
        sa.Column("priorities_json", postgresql.JSONB(), server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "hard_requirements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("buyer_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("buyer_profiles.id", ondelete="CASCADE")),
        sa.Column("metric", sa.String(80), nullable=False),
        sa.Column("operator", sa.String(10), nullable=False),
        sa.Column("threshold_number", sa.Float()),
        sa.Column("threshold_text", sa.String(100)),
        sa.Column("unit", sa.String(30)),
        sa.Column("label", sa.String(255)),
    )
    op.create_table(
        "important_locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("buyer_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("buyer_profiles.id", ondelete="CASCADE")),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("place_id", sa.String(255)),
        sa.Column("formatted_address", sa.Text()),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("usual_day_type", sa.String(20)),
        sa.Column("departure_time_local", sa.Time()),
        sa.Column("timezone", sa.String(50), server_default="Asia/Singapore"),
        sa.Column("transport_mode", sa.String(30)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("is_complete", sa.Boolean(), server_default="false"),
    )
    op.create_index("ix_important_locations_buyer_profile", "important_locations", ["buyer_profile_id"])
    op.create_table(
        "extraction_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("comparison_sessions.id", ondelete="CASCADE")),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("cleaned_text", sa.Text()),
        sa.Column("source_label", sa.String(255)),
        sa.Column("source_url", sa.Text()),
        sa.Column("character_count", sa.Integer()),
        sa.Column("content_hash", sa.String(64), index=True),
        sa.Column("pipeline_version", sa.String(50)),
        sa.Column("model_name", sa.String(100)),
        sa.Column("model_version", sa.String(50)),
        sa.Column("prompt_version", sa.String(50)),
        sa.Column("schema_version", sa.String(50)),
        sa.Column("status", sa.String(50), server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "listing_inputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("comparison_sessions.id", ondelete="CASCADE")),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("extraction_attempts.id", ondelete="SET NULL")),
        sa.Column("raw_text", sa.Text()),
        sa.Column("cleaned_text", sa.Text()),
        sa.Column("candidates_json", postgresql.JSONB(), server_default="{}"),
        sa.Column("extraction_warnings", postgresql.JSONB(), server_default="[]"),
        sa.Column("agent_claims", postgresql.JSONB(), server_default="[]"),
        sa.Column("source_label", sa.String(255)),
        sa.Column("source_url", sa.Text()),
        sa.Column("property_category", sa.String(30), server_default="HDB"),
        sa.Column("input_method", sa.String(30), server_default="manual"),
        sa.Column("pipeline_version", sa.String(50)),
        sa.Column("model_name", sa.String(100)),
        sa.Column("model_version", sa.String(50)),
        sa.Column("prompt_version", sa.String(50)),
        sa.Column("schema_version", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "confirmed_listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("comparison_sessions.id", ondelete="CASCADE")),
        sa.Column("listing_input_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("listing_inputs.id", ondelete="SET NULL")),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("asking_price", sa.Float(), nullable=False),
        sa.Column("floor_area_sqm", sa.Float(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("normalized_address_key", sa.String(512), index=True),
        sa.Column("flat_type", sa.String(50), nullable=False),
        sa.Column("property_category", sa.String(30), server_default="HDB"),
        sa.Column("storey_band", sa.String(50)),
        sa.Column("immediate_costs", sa.Float(), server_default="0"),
        sa.Column("renovation_estimate", sa.Float()),
        sa.Column("lease_commencement_year", sa.Integer()),
        sa.Column("remaining_lease_years", sa.Float()),
        sa.Column("remaining_lease_status", sa.String(50), server_default="NOT_PROVIDED_BY_USER"),
        sa.Column("source_url", sa.Text()),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("field_provenance_json", postgresql.JSONB(), server_default="[]"),
        sa.UniqueConstraint("session_id", "normalized_address_key", "asking_price", name="uq_listing_dup"),
    )
    op.create_index("ix_confirmed_listings_session", "confirmed_listings", ["session_id"])
    op.create_table(
        "observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("confirmed_listings.id", ondelete="CASCADE")),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(50), server_default="USER"),
        sa.Column("verification_state", sa.String(50), server_default="UNVERIFIED"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "enrichment_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("confirmed_listings.id", ondelete="CASCADE")),
        sa.Column("enrichment_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), server_default="PENDING"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
    )
    op.create_index("ix_enrichment_runs_listing_type", "enrichment_runs", ["listing_id", "enrichment_type"])
    op.create_table(
        "enriched_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("confirmed_listings.id", ondelete="CASCADE")),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("value_json", postgresql.JSONB()),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("source", sa.String(100)),
        sa.Column("source_version", sa.String(50)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("confidence", sa.String(20), server_default="NONE"),
        sa.Column("assumptions_json", postgresql.JSONB(), server_default="[]"),
        sa.Column("provenance", sa.String(50), server_default="CALCULATED"),
    )
    op.create_index("ix_enriched_fields_listing_name", "enriched_fields", ["listing_id", "field_name"])
    op.create_table(
        "journey_estimates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("confirmed_listings.id", ondelete="CASCADE")),
        sa.Column("important_location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("important_locations.id", ondelete="CASCADE")),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("requested_day_type", sa.String(20), nullable=False),
        sa.Column("requested_time_local", sa.Time(), nullable=False),
        sa.Column("timezone", sa.String(50), server_default="Asia/Singapore"),
        sa.Column("resolved_departure_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("difference_from_fastest_seconds", sa.Integer()),
        sa.Column("is_fastest", sa.Boolean()),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("provider", sa.String(50)),
        sa.Column("provider_status", sa.String(100)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_journey_estimates_listing_location_mode", "journey_estimates", ["listing_id", "important_location_id", "mode"])
    op.create_table(
        "recommendation_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("comparison_sessions.id", ondelete="CASCADE"), index=True),
        sa.Column("trace_json", postgresql.JSONB(), nullable=False),
        sa.Column("inputs_hash", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(20)),
        sa.Column("scoring_version", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "external_api_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cache_key", sa.String(512), unique=True, index=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("request_summary", sa.Text()),
        sa.Column("response_json", postgresql.JSONB()),
        sa.Column("status", sa.String(30)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("external_api_cache")
    op.drop_table("recommendation_traces")
    op.drop_table("journey_estimates")
    op.drop_table("enriched_fields")
    op.drop_table("enrichment_runs")
    op.drop_table("observations")
    op.drop_table("confirmed_listings")
    op.drop_table("listing_inputs")
    op.drop_table("extraction_attempts")
    op.drop_table("important_locations")
    op.drop_table("hard_requirements")
    op.drop_table("buyer_profiles")
    op.drop_table("comparison_sessions")
