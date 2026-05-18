"""Drop guild_music_player_settings table — music player feature removed

Revision ID: a1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-04-28 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = 'f0a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('guild_music_player_settings')


def downgrade() -> None:
    op.create_table(
        'guild_music_player_settings',
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('autoplay', sa.Boolean(), nullable=False),
        sa.Column('random_order', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('guild_id'),
    )
