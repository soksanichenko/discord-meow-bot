"""domain_fixers

Revision ID: 7b0c01070796
Revises:
Create Date: 2024-06-08 00:20:08.625435

"""

from typing import Sequence, Union
from sqlalchemy.engine.reflection import Inspector

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b0c01070796'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    tables = inspector.get_table_names()
    # ### commands auto generated by Alembic - please adjust! ###
    dropped_tables = (
        'bot_options',
        'domain_fixers',
    )
    for dropped_table in dropped_tables:
        if dropped_table in tables:
            op.drop_table(dropped_table)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        'domain_fixers',
        sa.Column('original', sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column('fixer', sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column('enabled', sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.PrimaryKeyConstraint('original', name='domain_fixers_pkey'),
    )
    op.create_table(
        'bot_options',
        sa.Column('id', sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column('name', sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column('value', sa.TEXT(), autoincrement=False, nullable=False),
        sa.PrimaryKeyConstraint('id', name='bot_options_pkey'),
    )
    # ### end Alembic commands ###