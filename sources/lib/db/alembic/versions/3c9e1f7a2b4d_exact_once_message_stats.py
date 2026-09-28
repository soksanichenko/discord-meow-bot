"""exact-once message stats

Revision ID: 3c9e1f7a2b4d
Revises: bc25943c8c1d
Create Date: 2026-09-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3c9e1f7a2b4d'
down_revision: str | None = 'bc25943c8c1d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'stats_import_jobs',
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('since', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('guild_id'),
    )
    # Existing counts overlap with the live counter and cannot be trusted, so they
    # are wiped and rebuilt from history by a queued import.
    op.execute(
        'INSERT INTO stats_import_jobs (guild_id) '
        'SELECT guild_id FROM message_stats '
        'UNION SELECT guild_id FROM stats_import_progress'
    )
    op.execute('DELETE FROM message_stats')
    op.execute('DELETE FROM stats_import_progress')
    op.alter_column(
        'stats_import_progress',
        'last_message_id',
        new_column_name='oldest_id',
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.add_column(
        'stats_import_progress',
        sa.Column('newest_id', sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('stats_import_progress', 'newest_id')
    op.alter_column(
        'stats_import_progress',
        'oldest_id',
        new_column_name='last_message_id',
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.drop_table('stats_import_jobs')
