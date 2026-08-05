"""Associate destination-based requirements with an important location."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007_journey_requirement_location"
down_revision: Union[str, None] = "006_named_schools"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hard_requirements",
        sa.Column(
            "important_location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("important_locations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("hard_requirements", "important_location_id")
