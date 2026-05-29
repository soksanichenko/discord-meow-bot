"""Operations with DB table `twitch_auth`"""

from datetime import datetime

from sources.lib.db import AsyncSession
from sources.lib.db.models import TwitchAuth


async def get_auth() -> TwitchAuth | None:
    """Return the stored Twitch OAuth tokens, or None if not yet authorized.

    Returns:
        TwitchAuth row (always id=1), or None.
    """
    async with AsyncSession() as session:
        return await session.get(TwitchAuth, 1)


async def save_auth(
    access_token: str, refresh_token: str, expires_at: datetime
) -> None:
    """Upsert Twitch OAuth tokens (always row id=1).

    Args:
        access_token: Current Twitch user access token.
        refresh_token: Refresh token used to obtain new access tokens.
        expires_at: UTC datetime when the access token expires.
    """
    async with AsyncSession() as session:
        auth = TwitchAuth(
            id=1,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        await session.merge(auth)
        await session.commit()
