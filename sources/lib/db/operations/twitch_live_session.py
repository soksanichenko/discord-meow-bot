"""Operations with DB table `twitch_live_sessions`"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sources.lib.db import AsyncSession
from sources.lib.db.models import TwitchLiveSession, TwitchRelay


async def add_live_session(relay_id: int, discord_message_id: int | None) -> None:
    """Record an active live stream for a relay.

    No-op if a session for this relay already exists (edge case: duplicate stream.online).

    Args:
        relay_id: Primary key of the TwitchRelay row.
        discord_message_id: Discord message ID of the announcement, or None if the post failed.
    """
    async with AsyncSession() as session:
        stmt = (
            pg_insert(TwitchLiveSession)
            .values(relay_id=relay_id, discord_message_id=discord_message_id)
            .on_conflict_do_nothing(index_elements=['relay_id'])
        )
        await session.execute(stmt)
        await session.commit()


async def get_live_sessions_for_user(twitch_user_id: str) -> list[TwitchLiveSession]:
    """Return all live sessions for relays tracking a specific Twitch user.

    Args:
        twitch_user_id: Twitch numeric user ID.

    Returns:
        List of TwitchLiveSession rows associated with relays for this user.
    """
    async with AsyncSession() as session:
        result = await session.scalars(
            select(TwitchLiveSession)
            .join(TwitchRelay, TwitchLiveSession.relay_id == TwitchRelay.id)
            .where(TwitchRelay.twitch_user_id == twitch_user_id)
        )
        return list(result.all())


async def remove_live_session(session_id: int) -> None:
    """Delete a live session record after the stream ends.

    Args:
        session_id: Primary key of the TwitchLiveSession row.
    """
    async with AsyncSession() as session:
        obj = await session.get(TwitchLiveSession, session_id)
        if obj is not None:
            await session.delete(obj)
            await session.commit()
