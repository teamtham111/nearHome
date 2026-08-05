"""Persist separate listing subtype, model, storey and source metadata."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_listing_attributes"
down_revision: Union[str, None] = "003_hdb_carpark_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("confirmed_listings", sa.Column("flat_type_raw", sa.String(length=100), nullable=True))
    op.add_column("confirmed_listings", sa.Column("listing_flat_subtype", sa.String(length=50), nullable=True))
    op.add_column("confirmed_listings", sa.Column("flat_type_source", sa.String(length=50), nullable=True))
    op.add_column("confirmed_listings", sa.Column("flat_model", sa.String(length=100), nullable=True))
    op.add_column("confirmed_listings", sa.Column("flat_model_source", sa.String(length=50), nullable=True))
    op.add_column("confirmed_listings", sa.Column("storey_range", sa.String(length=50), nullable=True))
    op.add_column("confirmed_listings", sa.Column("storey_source", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("confirmed_listings", "storey_source")
    op.drop_column("confirmed_listings", "storey_range")
    op.drop_column("confirmed_listings", "flat_model_source")
    op.drop_column("confirmed_listings", "flat_model")
    op.drop_column("confirmed_listings", "flat_type_source")
    op.drop_column("confirmed_listings", "listing_flat_subtype")
    op.drop_column("confirmed_listings", "flat_type_raw")
