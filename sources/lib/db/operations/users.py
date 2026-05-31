"""Operations with DB table `users`"""

from sources.lib.db import AsyncSession
from sources.lib.db.crud.base import CRUDBase
from sources.lib.db.models import User


async def get_user(user_id: int) -> User | None:
    """Get a user from DB by ID."""
    async with AsyncSession() as session:
        return await CRUDBase(session).get(User, id=user_id)


async def upsert_user(user_id: int, name: str, timezone: str) -> None:
    """Create or update a user record in DB."""
    async with AsyncSession() as session:
        await CRUDBase(session).upsert(
            User,
            filters={'id': user_id},
            updates={'name': name, 'timezone': timezone},
        )
