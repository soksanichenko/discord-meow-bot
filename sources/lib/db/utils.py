"""DB utilities"""

from sqlalchemy_utils import (
    database_exists,
    create_database,
)
from sources.config import config
from sources.lib.db import AsyncSession
from sources.lib.db.models import Base
from sources.lib.utils import Logger


async def create_db_if_not_exists():
    """
    Create DB and its initial objects if they don't exist
    :return:
    """
    Logger().info('Create DB if not exists')
    async with AsyncSession() as db_session:
        if not database_exists(config.sync_db_url):
            create_database(config.sync_db_url)
        async with db_session.bind.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
