"""add_random_order_to_music_player_settings

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-04-22 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: str | None = 'd1e2f3a4b5c6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add random_order column to guild_music_player_settings."""
    op.add_column(
        'guild_music_player_settings',
        sa.Column('random_order', sa.Boolean(), nullable=False, server_default='false'),
    )


def downgrade() -> None:
    """Remove random_order column from guild_music_player_settings."""
    op.drop_column('guild_music_player_settings', 'random_order')
