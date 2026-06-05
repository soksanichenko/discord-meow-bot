"""add voice_channels table

Revision ID: a2b3c4d5e6f7
Revises: f8a9b0c1d2e3
Create Date: 2026-06-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a2b3c4d5e6f7'
down_revision: str | None = '60b414c81cf1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'voice_channels',
        sa.Column('channel_id', sa.BigInteger(), nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('channel_id'),
    )


def downgrade() -> None:
    op.drop_table('voice_channels')
