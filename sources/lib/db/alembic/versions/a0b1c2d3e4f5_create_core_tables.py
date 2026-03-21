"""create_core_tables

Revision ID: a0b1c2d3e4f5
Revises: 7b0c01070796
Create Date: 2026-03-21 00:00:01.000000

Creates guilds and users tables if they do not already exist.
On existing deployments these tables were created by Base.metadata.create_all,
so the check prevents duplicate-table errors during migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = 'a0b1c2d3e4f5'
down_revision: Union[str, None] = '7b0c01070796'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create guilds and users tables if they do not exist."""
    conn = op.get_bind()
    existing = Inspector.from_engine(conn).get_table_names()

    if 'guilds' not in existing:
        op.create_table(
            'guilds',
            sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=False),
            sa.Column('name', sa.Text(), nullable=False),
        )

    if 'users' not in existing:
        op.create_table(
            'users',
            sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=False),
            sa.Column('name', sa.Text(), nullable=False),
            sa.Column('timezone', sa.Text(), nullable=False),
        )


def downgrade() -> None:
    """Drop guilds and users tables."""
    op.drop_table('users')
    op.drop_table('guilds')
