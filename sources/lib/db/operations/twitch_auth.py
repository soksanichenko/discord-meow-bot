"""Operations with DB table `twitch_auth`"""

from dataclasses import dataclass
from datetime import datetime

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sources.lib.db import AsyncSession
from sources.lib.db.models import TwitchAuth
from sources.lib.utils.crypto import decrypt, encrypt
from sources.lib.utils.logger import Logger

_logger = Logger()


@dataclass
class DecryptedTwitchAuth:
    """Twitch OAuth tokens with access_token/refresh_token already decrypted."""

    access_token: str
    refresh_token: str
    expires_at: datetime


async def get_auth() -> DecryptedTwitchAuth | None:
    """Return the stored Twitch OAuth tokens, decrypted, or None if not authorized.

    Rows written before encryption was introduced hold plaintext tokens. If
    decryption fails, the raw values are treated as legacy plaintext and
    transparently re-saved through save_auth() (encrypted), so this only
    happens once per row.

    Returns:
        DecryptedTwitchAuth, or None.
    """
    async with AsyncSession() as session:
        row = await session.scalar(select(TwitchAuth).where(TwitchAuth.id == 1))
    if row is None:
        return None

    try:
        access_token = decrypt(row.access_token)
        refresh_token = decrypt(row.refresh_token)
    except InvalidToken:
        _logger.warning(
            'Twitch auth row was stored in plaintext; migrating to encrypted storage'
        )
        access_token = row.access_token
        refresh_token = row.refresh_token
        await save_auth(access_token, refresh_token, row.expires_at)

    return DecryptedTwitchAuth(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=row.expires_at,
    )


async def save_auth(
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
) -> None:
    """Upsert the Twitch OAuth token row (always id=1), encrypted at rest.

    Args:
        access_token: New Twitch user access token.
        refresh_token: New Twitch refresh token.
        expires_at: UTC datetime when the access token expires.
    """
    encrypted_access = encrypt(access_token)
    encrypted_refresh = encrypt(refresh_token)
    async with AsyncSession() as session:
        stmt = (
            pg_insert(TwitchAuth)
            .values(
                id=1,
                access_token=encrypted_access,
                refresh_token=encrypted_refresh,
                expires_at=expires_at,
            )
            .on_conflict_do_update(
                index_elements=['id'],
                set_={
                    'access_token': encrypted_access,
                    'refresh_token': encrypted_refresh,
                    'expires_at': expires_at,
                },
            )
        )
        await session.execute(stmt)
        await session.commit()
