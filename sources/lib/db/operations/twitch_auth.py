"""Operations with DB table `twitch_auth`"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sources.lib.db import AsyncSession
from sources.lib.db.models import TwitchAuth


async def get_auth() -> TwitchAuth | None:
    """Return the stored Twitch OAuth tokens, or None if not yet authorized.

    Returns:
        TwitchAuth row, or None.
    """
    async with AsyncSession() as session:
        return await session.scalar(select(TwitchAuth).where(TwitchAuth.id == 1))


async def save_auth(
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
) -> None:
    """Upsert the Twitch OAuth token row (always id=1).

    Args:
        access_token: New Twitch user access token.
        refresh_token: New Twitch refresh token.
        expires_at: UTC datetime when the access token expires.
    """
    async with AsyncSession() as session:
        stmt = (
            pg_insert(TwitchAuth)
            .values(
                id=1,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
            )
            .on_conflict_do_update(
                index_elements=['id'],
                set_={
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'expires_at': expires_at,
                },
            )
        )
        await session.execute(stmt)
        await session.commit()
