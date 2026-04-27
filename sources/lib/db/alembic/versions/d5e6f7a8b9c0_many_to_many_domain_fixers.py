"""many_to_many_domain_fixers

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-03-21 00:00:03.000000

Replaces the guild-scoped flat domain_fixers table with a normalised M2M design:
  - domain_fixers: unique rules, deduplicated across guilds
  - guild_domain_fixers: junction linking guilds to their rules

Default rules are seeded once into domain_fixers and linked to every existing guild.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: str | None = 'c4d5e6f7a8b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_FIXERS = [
    {'source_domain': 'reddit.com',  'replacement_domain': 'rxddit',    'override_subdomain': None},
    {'source_domain': 'x.com',       'replacement_domain': 'fixupx',    'override_subdomain': None},
    {'source_domain': 'twitter.com', 'replacement_domain': 'fxtwitter',  'override_subdomain': None},
    {'source_domain': 'tiktok.com',  'replacement_domain': 'tnktok',    'override_subdomain': None},
]


def upgrade() -> None:
    """Replace flat guild-scoped table with normalised M2M schema."""
    conn = op.get_bind()

    guild_ids = [
        row[0] for row in conn.execute(sa.text('SELECT id FROM guilds')).fetchall()
    ]

    op.drop_table('domain_fixers')

    op.create_table(
        'domain_fixers',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('source_domain', sa.Text(), nullable=False),
        sa.Column('replacement_domain', sa.Text(), nullable=False),
        sa.Column('override_subdomain', sa.Text(), nullable=True),
    )
    # NULLS NOT DISTINCT ensures (reddit.com, rxddit, NULL) is deduplicated correctly.
    # Supported on PostgreSQL 15+.
    op.execute(
        'CREATE UNIQUE INDEX uq_domain_fixers_rule ON domain_fixers '
        '(source_domain, replacement_domain, override_subdomain) NULLS NOT DISTINCT'
    )

    guild_domain_fixers = op.create_table(
        'guild_domain_fixers',
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('domain_fixer_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['domain_fixer_id'], ['domain_fixers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('guild_id', 'domain_fixer_id'),
    )

    if not guild_ids:
        return

    fixer_ids = []
    for fixer in _DEFAULT_FIXERS:
        row = conn.execute(
            sa.text(
                'INSERT INTO domain_fixers (source_domain, replacement_domain, override_subdomain) '
                'VALUES (:source_domain, :replacement_domain, :override_subdomain) '
                'RETURNING id'
            ),
            fixer,
        ).fetchone()
        fixer_ids.append(row[0])

    op.bulk_insert(guild_domain_fixers, [
        {'guild_id': guild_id, 'domain_fixer_id': fixer_id}
        for guild_id in guild_ids
        for fixer_id in fixer_ids
    ])


def downgrade() -> None:
    """Restore flat guild-scoped domain_fixers table."""
    conn = op.get_bind()

    rows = conn.execute(sa.text(
        'SELECT gdf.guild_id, df.source_domain, df.replacement_domain, df.override_subdomain '
        'FROM guild_domain_fixers gdf '
        'JOIN domain_fixers df ON df.id = gdf.domain_fixer_id'
    )).fetchall()

    op.drop_table('guild_domain_fixers')
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

    if rows:
        op.bulk_insert(domain_fixers, [
            {
                'guild_id': r[0],
                'source_domain': r[1],
                'replacement_domain': r[2],
                'override_subdomain': r[3],
            }
            for r in rows
        ])
