"""Operations with DB table `auto_responders`."""

from datetime import UTC, datetime

from sqlalchemy import delete, select

from sources.lib.db import AsyncSession
from sources.lib.db.crud.base import CRUDBase
from sources.lib.db.models import AutoResponder


async def get_auto_responder(guild_id: int, user_id: int) -> AutoResponder | None:
    """Return the active (non-expired) auto-responder for a user in a guild.

    Args:
        guild_id: Discord guild ID.
        user_id: Discord user ID of the mentioned user.

    Returns:
        The AutoResponder instance, or None if none exists or it has expired.
    """
    async with AsyncSession() as session:
        now = datetime.now(tz=UTC)
        stmt = select(AutoResponder).where(
            AutoResponder.guild_id == guild_id,
            AutoResponder.user_id == user_id,
            (AutoResponder.expires_at.is_(None)) | (AutoResponder.expires_at > now),
        )
        return (await session.scalars(stmt)).one_or_none()


async def upsert_auto_responder(
    guild_id: int,
    user_id: int,
    response_text: str,
    expires_at: datetime | None,
) -> None:
    """Create or update the auto-responder for a user in a guild.

    Args:
        guild_id: Discord guild ID.
        user_id: Discord user ID.
        response_text: Message the bot will send when the user is mentioned.
        expires_at: When this responder expires, or None to never expire.
    """
    async with AsyncSession() as session:
        await CRUDBase(session).upsert(
            AutoResponder,
            filters={'guild_id': guild_id, 'user_id': user_id},
            updates={'response_text': response_text, 'expires_at': expires_at},
        )


async def delete_auto_responder(guild_id: int, user_id: int) -> bool:
    """Delete the auto-responder for a user in a guild.

    Args:
        guild_id: Discord guild ID.
        user_id: Discord user ID.

    Returns:
        True if a row was deleted, False if none existed.
    """
    async with AsyncSession() as session:
        result = await session.execute(
            delete(AutoResponder).where(
                AutoResponder.guild_id == guild_id,
                AutoResponder.user_id == user_id,
            )
        )
        await session.commit()
        return result.rowcount > 0


async def list_auto_responders(guild_id: int) -> list[AutoResponder]:
    """Return all active (non-expired) auto-responders for a guild.

    Args:
        guild_id: Discord guild ID.

    Returns:
        List of active AutoResponder instances ordered by user_id.
    """
    async with AsyncSession() as session:
        now = datetime.now(tz=UTC)
        stmt = (
            select(AutoResponder)
            .where(
                AutoResponder.guild_id == guild_id,
                (AutoResponder.expires_at.is_(None)) | (AutoResponder.expires_at > now),
            )
            .order_by(AutoResponder.user_id)
        )
        return list((await session.scalars(stmt)).all())


async def delete_expired_auto_responders() -> int:
    """Delete all auto-responders whose expiry has passed.

    Returns:
        Number of rows deleted.
    """
    async with AsyncSession() as session:
        now = datetime.now(tz=UTC)
        result = await session.execute(
            delete(AutoResponder).where(
                AutoResponder.expires_at.isnot(None),
                AutoResponder.expires_at <= now,
            )
        )
        await session.commit()
        return result.rowcount
