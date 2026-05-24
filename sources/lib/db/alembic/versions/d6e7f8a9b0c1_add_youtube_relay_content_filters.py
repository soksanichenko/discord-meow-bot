"""add youtube_relay content filters

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd6e7f8a9b0c1'
down_revision: str | None = 'c5d6e7f8a9b0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'youtube_relays',
        sa.Column('post_videos', sa.Boolean(), nullable=False, server_default='true'),
    )
    op.add_column(
        'youtube_relays',
        sa.Column('post_shorts', sa.Boolean(), nullable=False, server_default='true'),
    )
    op.add_column(
        'youtube_relays',
        sa.Column('post_lives', sa.Boolean(), nullable=False, server_default='true'),
    )


def downgrade() -> None:
    op.drop_column('youtube_relays', 'post_lives')
    op.drop_column('youtube_relays', 'post_shorts')
    op.drop_column('youtube_relays', 'post_videos')
