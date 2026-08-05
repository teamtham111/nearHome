"""Allow buyer profiles to retain multiple named schools."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006_named_schools"
down_revision: Union[str, None] = "005_canonical_lease_months"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "buyer_profiles",
        sa.Column(
            "named_schools_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        "UPDATE buyer_profiles SET named_schools_json = jsonb_build_array(named_school) "
        "WHERE named_school IS NOT NULL AND btrim(named_school) <> ''"
    )


def downgrade() -> None:
    op.drop_column("buyer_profiles", "named_schools_json")
