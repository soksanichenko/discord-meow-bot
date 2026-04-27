"""add_guild_music_player_settings

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-04-22 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: str | None = 'c0d1e2f3a4b5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create guild_music_player_settings table."""
    op.create_table(
        'guild_music_player_settings',
        sa.Column(
            'guild_id',
            sa.BigInteger(),
            sa.ForeignKey('guilds.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column('volume', sa.SmallInteger(), nullable=False, server_default='100'),
        sa.Column('autoplay', sa.Boolean(), nullable=False, server_default='false'),
    )


def downgrade() -> None:
    """Drop guild_music_player_settings table."""
    op.drop_table('guild_music_player_settings')
