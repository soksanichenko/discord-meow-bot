"""add youtube_relay custom notification messages

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-05-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e7f8a9b0c1d2'
down_revision: str | None = 'd6e7f8a9b0c1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'youtube_relays', sa.Column('message_video', sa.Text(), nullable=True)
    )
    op.add_column(
        'youtube_relays', sa.Column('message_short', sa.Text(), nullable=True)
    )
    op.add_column('youtube_relays', sa.Column('message_live', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('youtube_relays', 'message_live')
    op.drop_column('youtube_relays', 'message_short')
    op.drop_column('youtube_relays', 'message_video')
