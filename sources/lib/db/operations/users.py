"""Operations with DB table `users`"""

from sqlalchemy import select

from sources.lib.db import AsyncSession
from sources.lib.db.crud.base import CRUDBase
from sources.lib.db.models import User


async def get_user(user_id: int) -> User | None:
    """Get a user from DB by ID."""
    async with AsyncSession() as session:
        return await CRUDBase(session).get(User, id=user_id)


async def get_users_by_ids(user_ids: list[int]) -> list[User]:
    """Return User rows for the given IDs that have a timezone set.

    Args:
        user_ids: Discord user IDs to look up.
    """
    if not user_ids:
        return []
    async with AsyncSession() as session:
        stmt = (
            select(User)
            .where(User.id.in_(user_ids))
            .where(User.timezone.isnot(None))
            .order_by(User.name)
        )
        return list((await session.scalars(stmt)).all())


async def upsert_user(user_id: int, name: str, timezone: str) -> None:
    """Create or update a user record in DB."""
    async with AsyncSession() as session:
        await CRUDBase(session).upsert(
            User,
            filters={'id': user_id},
            updates={'name': name, 'timezone': timezone},
        )
