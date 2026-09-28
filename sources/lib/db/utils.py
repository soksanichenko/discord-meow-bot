"""DB utilities"""

from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from sources.config import config
from sources.lib.utils.logger import Logger


def _maintenance_url() -> str:
    """config.sync_db_url pointed at the `postgres` maintenance database instead of the app's own."""
    parts = urlsplit(config.sync_db_url)
    return urlunsplit(('postgresql', parts.netloc, '/postgres', '', ''))


async def create_db_if_not_exists():
    """Create the PostgreSQL database if it does not exist.

    Table creation and migrations are handled exclusively by alembic.
    """
    Logger().info('Create DB if not exists')
    database = urlsplit(config.sync_db_url).path.lstrip('/')
    with psycopg.connect(_maintenance_url(), autocommit=True) as conn:
        exists = conn.execute(
            'SELECT 1 FROM pg_database WHERE datname = %s', (database,)
        ).fetchone()
        if not exists:
            Logger().info('Database not found, creating')
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
