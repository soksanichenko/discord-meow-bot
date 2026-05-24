"""add youtube_relays table

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c5d6e7f8a9b0'
down_revision: str | None = 'b4c5d6e7f8a9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'youtube_relays',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('yt_channel_id', sa.Text(), nullable=False),
        sa.Column('yt_channel_title', sa.Text(), nullable=False),
        sa.Column('discord_channel_id', sa.BigInteger(), nullable=False),
        sa.Column('last_video_id', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_youtube_relays',
        'youtube_relays',
        ['guild_id', 'yt_channel_id', 'discord_channel_id'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_youtube_relays', table_name='youtube_relays')
    op.drop_table('youtube_relays')
