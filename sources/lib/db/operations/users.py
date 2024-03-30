"""
Operations with DB table `users`
"""

import typing

import discord
from sqlalchemy import select

from sources.lib.db import AsyncSession
from sources.lib.db.models import User


async def get_user(
    db_session: AsyncSession,
    user_id: int,
) -> typing.Optional[User]:
    """Get user by ID"""
    user = select(User).where(User.id == user_id)
    return (await db_session.scalars(user)).one_or_none()


async def add_user(
    discord_user: discord.User,
    user_timezone: str,
):
    """Add a user to a database"""
    async with AsyncSession() as db_session:
        async with db_session.begin():
            user = await get_user(
                db_session=db_session,
                user_id=discord_user.id,
            )
            if user is None:
                user = User(
                    id=discord_user.id,
                    name=discord_user.name,
                    timezone=user_timezone,
                )
                db_session.add(user)
            elif user.name != discord_user.name:
                user.name = discord_user.name
                user.timezone = user_timezone
                db_session.add(user)


async def get_user_timezone(
    discord_user: discord.User,
) -> typing.Optional[str]:
    """Get a user timezone"""
    async with AsyncSession() as db_session:
        user = await get_user(
            db_session=db_session,
            user_id=discord_user.id,
        )
        if user is not None:
            return user.timezone
