"""Store source-aware canonical HDB lease months."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005_canonical_lease_months"
down_revision: Union[str, None] = "004_listing_attributes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("confirmed_listings", sa.Column("remaining_lease_months", sa.Integer(), nullable=True))
    op.add_column("confirmed_listings", sa.Column("remaining_lease_source", sa.String(length=50), nullable=True))
    op.add_column("confirmed_listings", sa.Column("remaining_lease_confidence", sa.String(length=20), nullable=True))
    op.add_column("confirmed_listings", sa.Column("remaining_lease_as_of_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("confirmed_listings", "remaining_lease_as_of_date")
    op.drop_column("confirmed_listings", "remaining_lease_confidence")
    op.drop_column("confirmed_listings", "remaining_lease_source")
    op.drop_column("confirmed_listings", "remaining_lease_months")
