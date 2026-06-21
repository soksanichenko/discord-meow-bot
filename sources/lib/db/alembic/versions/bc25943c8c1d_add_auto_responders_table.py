"""add auto_responders table

Revision ID: bc25943c8c1d
Revises: a2b3c4d5e6f7
Create Date: 2026-06-21 11:11:33.812465

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bc25943c8c1d'
down_revision: str | None = 'a2b3c4d5e6f7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'auto_responders',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('response_text', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_auto_responders', 'auto_responders', ['guild_id', 'user_id'], unique=True
    )


def downgrade() -> None:
    op.drop_index('uq_auto_responders', table_name='auto_responders')
    op.drop_table('auto_responders')
