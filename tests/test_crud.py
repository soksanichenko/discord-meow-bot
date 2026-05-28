"""Tests for the generic CRUD base layer (crud/base.py).

The session is injected, so tests pass a plain AsyncMock — no DB needed.
"""

from unittest.mock import AsyncMock, MagicMock

from sources.lib.db.crud.base import (
    create_db_entity,
    delete_db_entity,
    delete_db_entity_if_exists,
    get_db_entity,
    update_db_entity,
    update_db_entity_or_create,
)
from sources.lib.db.models import User


def _session(*, scalars_one=None, get=None):
    """Build a minimal mock AsyncSession for CRUD tests."""
    session = AsyncMock()
    # session.add() is synchronous in SQLAlchemy — override the AsyncMock default.
    session.add = MagicMock()

    scalars_mock = MagicMock()
    scalars_mock.one_or_none.return_value = scalars_one
    session.scalars.return_value = scalars_mock

    session.get.return_value = get
    return session


class TestGetDbEntity:
    async def test_returns_entity_when_found(self):
        user = User(id=1, name='Alice')
        session = _session(scalars_one=user)
        result = await get_db_entity(session, User, id=1)
        assert result is user

    async def test_returns_none_when_not_found(self):
        session = _session(scalars_one=None)
        result = await get_db_entity(session, User, id=999)
        assert result is None


class TestCreateDbEntity:
    async def test_adds_entity_and_commits(self):
        session = _session()
        await create_db_entity(session, User, id=1, name='Alice')
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        assert isinstance(added, User)
        assert added.id == 1
        assert added.name == 'Alice'
        session.commit.assert_awaited_once()


class TestUpdateDbEntity:
    async def test_sets_attributes_and_commits(self):
        session = _session()
        entity = MagicMock()
        await update_db_entity(session, entity, name='Bob', timezone='UTC')
        assert entity.name == 'Bob'
        assert entity.timezone == 'UTC'
        session.add.assert_called_once_with(entity)
        session.commit.assert_awaited_once()


class TestDeleteDbEntity:
    async def test_deletes_and_commits(self):
        session = _session()
        entity = MagicMock()
        await delete_db_entity(session, entity)
        session.delete.assert_awaited_once_with(entity)
        session.commit.assert_awaited_once()


class TestDeleteDbEntityIfExists:
    async def test_deletes_when_entity_exists(self):
        entity = MagicMock()
        session = _session(scalars_one=entity)
        await delete_db_entity_if_exists(session, User, id=1)
        session.delete.assert_awaited_once_with(entity)
        session.commit.assert_awaited_once()

    async def test_no_op_when_entity_does_not_exist(self):
        session = _session(scalars_one=None)
        await delete_db_entity_if_exists(session, User, id=999)
        session.delete.assert_not_awaited()
        session.commit.assert_not_awaited()


class TestUpdateDbEntityOrCreate:
    async def test_creates_entity_when_not_found(self):
        session = _session(scalars_one=None)
        await update_db_entity_or_create(session, User, {'id': 1}, {'name': 'Alice'})
        session.add.assert_called_once()
        created = session.add.call_args[0][0]
        assert isinstance(created, User)
        assert created.id == 1
        assert created.name == 'Alice'
        session.commit.assert_awaited_once()

    async def test_updates_entity_when_found(self):
        existing = MagicMock()
        session = _session(scalars_one=existing)
        await update_db_entity_or_create(session, User, {'id': 1}, {'name': 'Bob'})
        assert existing.name == 'Bob'
        session.add.assert_called_once_with(existing)
        session.commit.assert_awaited_once()

    async def test_create_path_does_not_call_delete(self):
        session = _session(scalars_one=None)
        await update_db_entity_or_create(session, User, {'id': 2}, {'name': 'Carol'})
        session.delete.assert_not_awaited()
