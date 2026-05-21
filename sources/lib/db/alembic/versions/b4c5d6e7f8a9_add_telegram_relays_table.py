"""add telegram_relays table

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-05-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b4c5d6e7f8a9'
down_revision: str | None = 'a3b4c5d6e7f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'telegram_relays',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('tg_username', sa.Text(), nullable=False),
        sa.Column('discord_channel_id', sa.BigInteger(), nullable=False),
        sa.Column('last_entry_id', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_telegram_relays',
        'telegram_relays',
        ['guild_id', 'tg_username', 'discord_channel_id'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_telegram_relays', table_name='telegram_relays')
    op.drop_table('telegram_relays')
