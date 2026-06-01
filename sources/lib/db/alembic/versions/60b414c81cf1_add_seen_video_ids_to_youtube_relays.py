"""add seen_video_ids to youtube_relays

Revision ID: 60b414c81cf1
Revises: e4f5a6b7c8d9
Create Date: 2026-06-01 20:47:26.353673

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '60b414c81cf1'
down_revision: str | None = 'e4f5a6b7c8d9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'youtube_relays',
        sa.Column('seen_video_ids', sa.JSON(), server_default='[]', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('youtube_relays', 'seen_video_ids')
