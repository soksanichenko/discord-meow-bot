"""add_guild_settings_timezone

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-04-18 01:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a8b9c0d1e2f3'
down_revision: str | None = 'f7a8b9c0d1e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add timezone column to guild_settings."""
    op.add_column('guild_settings', sa.Column('timezone', sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove timezone column from guild_settings."""
    op.drop_column('guild_settings', 'timezone')
