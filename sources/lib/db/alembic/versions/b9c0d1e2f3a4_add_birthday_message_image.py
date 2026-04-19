"""add_birthday_message_image

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-04-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b9c0d1e2f3a4'
down_revision: Union[str, None] = 'a8b9c0d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add birthday_message and birthday_image_path columns to guild_settings."""
    op.add_column('guild_settings', sa.Column('birthday_message', sa.Text(), nullable=True))
    op.add_column('guild_settings', sa.Column('birthday_image_path', sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove birthday_message and birthday_image_path columns from guild_settings."""
    op.drop_column('guild_settings', 'birthday_image_path')
    op.drop_column('guild_settings', 'birthday_message')
