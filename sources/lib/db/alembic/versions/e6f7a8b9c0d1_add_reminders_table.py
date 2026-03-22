"""add_reminders_table

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-03-22 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the reminders table."""
    op.create_table(
        'reminders',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('channel_id', sa.BigInteger(), nullable=False),
        sa.Column('message_url', sa.Text(), nullable=True),
        sa.Column('message_content', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('remind_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_sent', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index('ix_reminders_user_id_is_sent', 'reminders', ['user_id', 'is_sent'])


def downgrade() -> None:
    """Drop the reminders table."""
    op.drop_index('ix_reminders_user_id_is_sent', table_name='reminders')
    op.drop_table('reminders')
