"""add twitch_live_sessions table

Revision ID: c2d3e4f5a6b7
Revises: b0c1d2e3f4a5
Create Date: 2026-05-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c2d3e4f5a6b7'
down_revision: str | None = 'b0c1d2e3f4a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'twitch_live_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('relay_id', sa.Integer(), nullable=False),
        sa.Column('discord_message_id', sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(['relay_id'], ['twitch_relays.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_twitch_live_sessions', 'twitch_live_sessions', ['relay_id'], unique=True
    )


def downgrade() -> None:
    op.drop_index('uq_twitch_live_sessions', table_name='twitch_live_sessions')
    op.drop_table('twitch_live_sessions')
