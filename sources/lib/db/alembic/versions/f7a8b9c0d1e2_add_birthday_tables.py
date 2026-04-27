"""add_birthday_tables

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-04-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: str | None = 'e6f7a8b9c0d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create guild_settings and guild_member_birthdays tables."""
    op.create_table(
        'guild_settings',
        sa.Column(
            'guild_id',
            sa.BigInteger(),
            sa.ForeignKey('guilds.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column('birthday_channel_id', sa.BigInteger(), nullable=True),
        sa.Column('birthday_role_id', sa.BigInteger(), nullable=True),
    )

    op.create_table(
        'guild_member_birthdays',
        sa.Column(
            'guild_id',
            sa.BigInteger(),
            sa.ForeignKey('guilds.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column('user_id', sa.BigInteger(), primary_key=True),
        sa.Column('birthday_day', sa.SmallInteger(), nullable=False),
        sa.Column('birthday_month', sa.SmallInteger(), nullable=False),
        sa.Column('birth_year', sa.SmallInteger(), nullable=True),
        sa.Column('last_announced_year', sa.SmallInteger(), nullable=True),
    )
    op.create_index(
        'ix_guild_member_birthdays_month_day',
        'guild_member_birthdays',
        ['birthday_month', 'birthday_day'],
    )


def downgrade() -> None:
    """Drop guild_member_birthdays and guild_settings tables."""
    op.drop_index('ix_guild_member_birthdays_month_day', table_name='guild_member_birthdays')
    op.drop_table('guild_member_birthdays')
    op.drop_table('guild_settings')
