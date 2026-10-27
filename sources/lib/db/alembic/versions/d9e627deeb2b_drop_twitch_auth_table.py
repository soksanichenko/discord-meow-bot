"""drop twitch_auth table

The Twitch relay now receives events over EventSub webhooks and authenticates
with an app access token, so the stored user OAuth tokens are no longer used.
Downgrading recreates the empty table; the tokens are not restorable, so run
/twitch-relay authorize again after rolling the code back.

Revision ID: d9e627deeb2b
Revises: 3c9e1f7a2b4d
Create Date: 2026-10-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd9e627deeb2b'
down_revision: str | None = '3c9e1f7a2b4d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table('twitch_auth')


def downgrade() -> None:
    op.create_table(
        'twitch_auth',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('access_token', sa.Text(), nullable=False),
        sa.Column('refresh_token', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
