"""add youtube_live_sessions table

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-05-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f8a9b0c1d2e3'
down_revision: str | None = 'e7f8a9b0c1d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'youtube_live_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('relay_id', sa.Integer(), nullable=False),
        sa.Column('video_id', sa.Text(), nullable=False),
        sa.Column('discord_message_id', sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ['relay_id'], ['youtube_relays.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_youtube_live_sessions',
        'youtube_live_sessions',
        ['relay_id', 'video_id'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_youtube_live_sessions', table_name='youtube_live_sessions')
    op.drop_table('youtube_live_sessions')
