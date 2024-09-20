"""Base operations with any DB table"""

from typing import (
    Any,
    Type,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sources.lib.db.models import Base


async def get_db_entity(
    db_session: AsyncSession,
    table_class: Type[Base],
    **kwargs,
):
    """Get a DB entity by the params"""
    user = select(table_class).filter_by(**kwargs)
    return (await db_session.scalars(user)).one_or_none()


async def create_db_entity(db_session: AsyncSession, table_class: Type[Base], **kwargs):
    """Add a new DB entity"""
    db_entity = table_class(
        **kwargs,
    )
    db_session.add(db_entity)
    await db_session.commit()


async def update_db_entity(
    db_session: AsyncSession,
    db_entity: Type[Base],
    **kwargs,
):
    """Update an existing DB entity"""
    for key, value in kwargs.items():
        setattr(db_entity, key, value)
    db_session.add(db_entity)
    await db_session.commit()


async def delete_db_entity(
    db_session: AsyncSession,
    db_entity: Type[Base],
):
    """Delete an existing DB entity"""
    await db_session.delete(db_entity)


async def delete_db_entity_if_exists(
    db_session: AsyncSession,
    table_class: Type[Base],
    **kwargs,
):
    """Delete a DB entity if it exists"""
    db_entity = await get_db_entity(
        db_session=db_session,
        table_class=table_class,
        **kwargs,
    )
    if db_entity is not None:
        await delete_db_entity(
            db_session=db_session,
            db_entity=db_entity,
        )


async def update_db_entity_or_create(
    db_session: AsyncSession,
    table_class: Type[Base],
    filters: dict[str, Any],
    updates: dict[str, Any],
):
    """Update a DB entity if it exists or create if it doesn't exist"""
    db_entity = await get_db_entity(
        db_session=db_session,
        table_class=table_class,
        **filters,
    )
    if db_entity is None:
        filters.update(updates)
        await create_db_entity(
            db_session=db_session,
            table_class=table_class,
            **filters,
        )
    else:
        await update_db_entity(
            db_session=db_session,
            db_entity=db_entity,
            **updates,
        )
