"""add twitch_relays table

Revision ID: 9a8b7c6d5e4f
Revises: f8a9b0c1d2e3
Create Date: 2026-05-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '9a8b7c6d5e4f'
down_revision: str | None = 'f8a9b0c1d2e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'twitch_relays',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('twitch_user_id', sa.Text(), nullable=False),
        sa.Column('twitch_login', sa.Text(), nullable=False),
        sa.Column('discord_channel_id', sa.BigInteger(), nullable=False),
        sa.Column('custom_message', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_twitch_relays',
        'twitch_relays',
        ['guild_id', 'twitch_user_id', 'discord_channel_id'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_twitch_relays', table_name='twitch_relays')
    op.drop_table('twitch_relays')
