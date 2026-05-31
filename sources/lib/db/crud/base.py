"""Generic CRUD operations via a bound SQLAlchemy async session."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sources.lib.db.models import Base


class CRUDBase:
    """Generic CRUD helper bound to a single SQLAlchemy async session.

    Instantiate with an active session and call methods without repeating
    the session argument on every operation.

    Args:
        session: Active async session to execute all operations against.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise with an active async session.

        Args:
            session: The SQLAlchemy async session to use for all operations.
        """
        self._session = session

    async def get(self, table_class: type[Base], **kwargs) -> Base | None:
        """Return the first row matching kwargs, or None.

        Args:
            table_class: ORM model class to query.
            **kwargs: Column filter criteria passed to filter_by().
        """
        stmt = select(table_class).filter_by(**kwargs)
        return (await self._session.scalars(stmt)).one_or_none()

    async def create(self, table_class: type[Base], **kwargs) -> None:
        """Create and persist a new row.

        Args:
            table_class: ORM model class to instantiate.
            **kwargs: Column values for the new row.
        """
        entity = table_class(**kwargs)
        self._session.add(entity)
        await self._session.commit()

    async def update(self, entity: Base, **kwargs) -> None:
        """Update attributes on an existing row and commit.

        Args:
            entity: ORM instance to update.
            **kwargs: Attribute names and their new values.
        """
        for key, value in kwargs.items():
            setattr(entity, key, value)
        self._session.add(entity)
        await self._session.commit()

    async def delete(self, entity: Base) -> None:
        """Delete an existing row and commit.

        Args:
            entity: ORM instance to delete.
        """
        await self._session.delete(entity)
        await self._session.commit()

    async def delete_if_exists(self, table_class: type[Base], **kwargs) -> None:
        """Delete a row if it exists; no-op otherwise.

        Args:
            table_class: ORM model class to query.
            **kwargs: Column filter criteria.
        """
        entity = await self.get(table_class, **kwargs)
        if entity is not None:
            await self.delete(entity)

    async def upsert(
        self,
        table_class: type[Base],
        filters: dict[str, Any],
        updates: dict[str, Any],
    ) -> None:
        """Create a row if it does not exist, otherwise update it.

        Args:
            table_class: ORM model class.
            filters: Column values used to look up the existing row.
            updates: Column values to set on create or update.
        """
        entity = await self.get(table_class, **filters)
        if entity is None:
            await self.create(table_class, **{**filters, **updates})
        else:
            await self.update(entity, **updates)
