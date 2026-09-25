"""add_channel_assignment_active_unique_constraint

Revision ID: d1e2f3a4b5c6
Revises: c1d2e3f4a5b6
Create Date: 2026-09-24 13:55:00.000000

Adds a partial unique index on (slave_card_id, channel_number) for active assignments
where is_assigned = True and slave_card_id IS NOT NULL and channel_number IS NOT NULL.
This allows unassigned historical/discovered rows without conflicting while preventing
concurrent duplicate active assignments to the same physical channel.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_param_active_assignment
        ON asset_parameters (slave_card_id, channel_number)
        WHERE is_assigned = TRUE AND slave_card_id IS NOT NULL AND channel_number IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS uq_asset_param_active_assignment;
        """
    )
