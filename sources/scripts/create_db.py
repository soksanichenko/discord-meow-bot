"""
Pre-run script for creating a DB and its initial objects
"""

import asyncio
from discord import utils

from sources.lib.db.utils import create_db_if_not_exists


async def main():
    """Main run function"""
    utils.setup_logging()
    await create_db_if_not_exists()


if __name__ == '__main__':
    asyncio.run(main())
