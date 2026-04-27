"""add_domain_fixers_table

Revision ID: b3c4d5e6f7a8
Revises: 7b0c01070796
Create Date: 2026-03-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: str | None = 'a0b1c2d3e4f5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_FIXERS = [
    {'source_domain': 'reddit.com',  'replacement_domain': 'rxddit',    'override_subdomain': None},
    {'source_domain': 'x.com',       'replacement_domain': 'fixupx',    'override_subdomain': None},
    {'source_domain': 'twitter.com', 'replacement_domain': 'fxtwitter',  'override_subdomain': None},
    {'source_domain': 'tiktok.com',  'replacement_domain': 'tnktok',    'override_subdomain': None},
]


def upgrade() -> None:
    """Create domain_fixers table and seed default entries."""
    domain_fixers = op.create_table(
        'domain_fixers',
        sa.Column('source_domain', sa.Text(), primary_key=True, nullable=False),
        sa.Column('replacement_domain', sa.Text(), nullable=False),
        sa.Column('override_subdomain', sa.Text(), nullable=True),
    )
    op.bulk_insert(domain_fixers, _DEFAULT_FIXERS)


def downgrade() -> None:
    """Drop domain_fixers table."""
    op.drop_table('domain_fixers')
