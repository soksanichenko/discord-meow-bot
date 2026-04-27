"""add_music_links_channels

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-04-19 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c0d1e2f3a4b5'
down_revision: str | None = 'b9c0d1e2f3a4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create music_links_channels table."""
    op.create_table(
        'music_links_channels',
        sa.Column(
            'guild_id',
            sa.BigInteger(),
            sa.ForeignKey('guilds.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column('channel_id', sa.BigInteger(), primary_key=True),
    )


def downgrade() -> None:
    """Drop music_links_channels table."""
    op.drop_table('music_links_channels')
