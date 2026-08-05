"""Retain raw listing subtype separately from canonical HDB fields."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008_raw_listing_subtype"
down_revision: Union[str, None] = "007_journey_requirement_location"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("confirmed_listings", sa.Column("raw_listing_subtype", sa.String(length=50), nullable=True))
    op.add_column(
        "confirmed_listings",
        sa.Column(
            "subtype_conflicts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        "UPDATE confirmed_listings SET raw_listing_subtype = listing_flat_subtype "
        "WHERE listing_flat_subtype IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("confirmed_listings", "subtype_conflicts")
    op.drop_column("confirmed_listings", "raw_listing_subtype")
