"""add_guild_id_to_domain_fixers

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-03-21 00:00:02.000000

Replaces the global domain_fixers table with a guild-scoped one.
The old table (source_domain PK) is dropped and recreated with a composite
primary key (guild_id, source_domain) and a FK to guilds.
Default rules are seeded for every guild already present in the guilds table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_FIXERS = [
    {'source_domain': 'reddit.com',  'replacement_domain': 'rxddit',    'override_subdomain': None},
    {'source_domain': 'x.com',       'replacement_domain': 'fixupx',    'override_subdomain': None},
    {'source_domain': 'twitter.com', 'replacement_domain': 'fxtwitter',  'override_subdomain': None},
    {'source_domain': 'tiktok.com',  'replacement_domain': 'tnktok',    'override_subdomain': None},
]


def upgrade() -> None:
    """Drop global domain_fixers table, recreate it scoped to guilds, seed defaults."""
    conn = op.get_bind()

    guild_ids = [row[0] for row in conn.execute(sa.text('SELECT id FROM guilds')).fetchall()]

    op.drop_table('domain_fixers')
    domain_fixers = op.create_table(
        'domain_fixers',
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('source_domain', sa.Text(), nullable=False),
        sa.Column('replacement_domain', sa.Text(), nullable=False),
        sa.Column('override_subdomain', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('guild_id', 'source_domain'),
    )

    if guild_ids:
        op.bulk_insert(domain_fixers, [
            {'guild_id': guild_id, **fixer}
            for guild_id in guild_ids
            for fixer in _DEFAULT_FIXERS
        ])


def downgrade() -> None:
    """Restore global domain_fixers table without guild scope."""
    op.drop_table('domain_fixers')
    domain_fixers = op.create_table(
        'domain_fixers',
        sa.Column('source_domain', sa.Text(), primary_key=True, nullable=False),
        sa.Column('replacement_domain', sa.Text(), nullable=False),
        sa.Column('override_subdomain', sa.Text(), nullable=True),
    )
    op.bulk_insert(domain_fixers, [
        {'source_domain': 'reddit.com',  'replacement_domain': 'rxddit',   'override_subdomain': None},
        {'source_domain': 'x.com',       'replacement_domain': 'fixupx',   'override_subdomain': None},
        {'source_domain': 'twitter.com', 'replacement_domain': 'fxtwitter', 'override_subdomain': None},
        {'source_domain': 'tiktok.com',  'replacement_domain': 'tnktok',   'override_subdomain': None},
    ])
