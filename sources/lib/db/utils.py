"""DB utilities"""

from sqlalchemy_utils import (
    create_database,
    database_exists,
)

from sources.config import config
from sources.lib.utils import Logger


async def create_db_if_not_exists():
    """Create the PostgreSQL database if it does not exist.

    Table creation and migrations are handled exclusively by alembic.
    """
    Logger().info('Create DB if not exists')
    if not database_exists(config.sync_db_url):
        Logger().info('Database not found, creating')
        create_database(config.sync_db_url)
