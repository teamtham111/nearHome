"""Remove retired committed-cost and storey-band workflow columns."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_retire_storey_cost"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing hard requirements using the retired derived metric cannot be
    # evaluated after this release, so remove only those obsolete rules.
    op.execute(sa.text("DELETE FROM hard_requirements WHERE metric = 'COMMITTED_COST'"))
    op.drop_column("buyer_profiles", "immediate_costs_count_against_budget")
    op.drop_column("confirmed_listings", "immediate_costs")
    op.drop_column("confirmed_listings", "storey_band")


def downgrade() -> None:
    op.add_column(
        "confirmed_listings",
        sa.Column("storey_band", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "confirmed_listings",
        sa.Column("immediate_costs", sa.Float(), server_default="0", nullable=True),
    )
    op.add_column(
        "buyer_profiles",
        sa.Column("immediate_costs_count_against_budget", sa.Boolean(), server_default="true", nullable=True),
    )
