"""Operations with DB table `youtube_live_sessions`"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sources.lib.db import AsyncSession
from sources.lib.db.models import YouTubeLiveSession


async def add_live_session(
    relay_id: int, video_id: str, discord_message_id: int | None = None
) -> None:
    """Record a new live stream session to track. No-op if already tracked.

    Args:
        relay_id: Primary key of the YouTubeRelay row that posted the live notification.
        video_id: YouTube video ID of the live stream.
        discord_message_id: Snowflake ID of the Discord message that announced the stream.
    """
    async with AsyncSession() as session:
        stmt = (
            pg_insert(YouTubeLiveSession)
            .values(
                relay_id=relay_id,
                video_id=video_id,
                discord_message_id=discord_message_id,
            )
            .on_conflict_do_nothing(index_elements=['relay_id', 'video_id'])
        )
        await session.execute(stmt)
        await session.commit()


async def get_all_live_sessions() -> list[YouTubeLiveSession]:
    """Return all currently tracked live sessions.

    Returns:
        List of all YouTubeLiveSession rows.
    """
    async with AsyncSession() as session:
        result = await session.scalars(select(YouTubeLiveSession))
        return list(result.all())


async def remove_live_session(session_id: int) -> None:
    """Delete a live session record by primary key.

    Args:
        session_id: Primary key of the YouTubeLiveSession row.
    """
    async with AsyncSession() as session:
        obj = await session.get(YouTubeLiveSession, session_id)
        if obj is not None:
            await session.delete(obj)
            await session.commit()
