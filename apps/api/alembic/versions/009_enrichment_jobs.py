"""Add durable Cloud Tasks-backed enrichment jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "009_enrichment_jobs"
down_revision: str | None = "008_raw_listing_subtype"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enrichment_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("comparison_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("confirmed_listings.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("job_type", sa.String(length=80), nullable=False, server_default="SESSION_ENRICHMENT"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("progress_stage", sa.String(length=100), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("internal_error_detail", sa.Text(), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_enrichment_jobs_session", "enrichment_jobs", ["session_id"])
    op.create_index("ix_enrichment_jobs_listing", "enrichment_jobs", ["listing_id"])
    op.create_index("ix_enrichment_jobs_status", "enrichment_jobs", ["status"])
    op.create_index("ix_enrichment_jobs_created_at", "enrichment_jobs", ["created_at"])
    op.create_index(
        "uq_enrichment_jobs_active_session",
        "enrichment_jobs",
        ["session_id", "job_type"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_enrichment_jobs_active_session", table_name="enrichment_jobs")
    op.drop_index("ix_enrichment_jobs_created_at", table_name="enrichment_jobs")
    op.drop_index("ix_enrichment_jobs_status", table_name="enrichment_jobs")
    op.drop_index("ix_enrichment_jobs_listing", table_name="enrichment_jobs")
    op.drop_index("ix_enrichment_jobs_session", table_name="enrichment_jobs")
    op.drop_table("enrichment_jobs")
