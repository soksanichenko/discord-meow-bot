"""Operations with DB table `users`"""

from sources.lib.db import AsyncSession
from sources.lib.db.crud.base import get_db_entity, update_db_entity_or_create
from sources.lib.db.models import User


async def get_user(user_id: int) -> User | None:
    """Get a user from DB by ID."""
    async with AsyncSession() as db_session:
        return await get_db_entity(
            db_session=db_session,
            table_class=User,
            id=user_id,
        )


async def upsert_user(user_id: int, name: str, timezone: str) -> None:
    """Create or update a user record in DB."""
    async with AsyncSession() as db_session:
        await update_db_entity_or_create(
            db_session=db_session,
            table_class=User,
            filters={'id': user_id},
            updates={'name': name, 'timezone': timezone},
        )
