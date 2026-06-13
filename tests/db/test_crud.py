"""Tests for the generic CRUD base layer (crud/base.py).

The session is injected via CRUDBase.__init__, so tests pass a plain
AsyncMock — no DB needed.
"""

from unittest.mock import AsyncMock, MagicMock

from sources.lib.db.crud.base import CRUDBase
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


class TestGet:
    async def test_returns_entity_when_found(self):
        user = User(id=1, name='Alice')
        result = await CRUDBase(_session(scalars_one=user)).get(User, id=1)
        assert result is user

    async def test_returns_none_when_not_found(self):
        result = await CRUDBase(_session(scalars_one=None)).get(User, id=999)
        assert result is None


class TestCreate:
    async def test_adds_entity_and_commits(self):
        session = _session()
        await CRUDBase(session).create(User, id=1, name='Alice')
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        assert isinstance(added, User)
        assert added.id == 1
        assert added.name == 'Alice'
        session.commit.assert_awaited_once()


class TestUpdate:
    async def test_sets_attributes_and_commits(self):
        session = _session()
        entity = MagicMock()
        await CRUDBase(session).update(entity, name='Bob', timezone='UTC')
        assert entity.name == 'Bob'
        assert entity.timezone == 'UTC'
        session.add.assert_called_once_with(entity)
        session.commit.assert_awaited_once()


class TestDelete:
    async def test_deletes_and_commits(self):
        session = _session()
        entity = MagicMock()
        await CRUDBase(session).delete(entity)
        session.delete.assert_awaited_once_with(entity)
        session.commit.assert_awaited_once()


class TestDeleteIfExists:
    async def test_deletes_when_entity_exists(self):
        entity = MagicMock()
        session = _session(scalars_one=entity)
        await CRUDBase(session).delete_if_exists(User, id=1)
        session.delete.assert_awaited_once_with(entity)
        session.commit.assert_awaited_once()

    async def test_no_op_when_entity_does_not_exist(self):
        session = _session(scalars_one=None)
        await CRUDBase(session).delete_if_exists(User, id=999)
        session.delete.assert_not_awaited()
        session.commit.assert_not_awaited()


class TestUpsert:
    async def test_creates_entity_when_not_found(self):
        session = _session(scalars_one=None)
        await CRUDBase(session).upsert(User, {'id': 1}, {'name': 'Alice'})
        session.add.assert_called_once()
        created = session.add.call_args[0][0]
        assert isinstance(created, User)
        assert created.id == 1
        assert created.name == 'Alice'
        session.commit.assert_awaited_once()

    async def test_updates_entity_when_found(self):
        existing = MagicMock()
        session = _session(scalars_one=existing)
        await CRUDBase(session).upsert(User, {'id': 1}, {'name': 'Bob'})
        assert existing.name == 'Bob'
        session.add.assert_called_once_with(existing)
        session.commit.assert_awaited_once()

    async def test_create_path_does_not_call_delete(self):
        session = _session(scalars_one=None)
        await CRUDBase(session).upsert(User, {'id': 2}, {'name': 'Carol'})
        session.delete.assert_not_awaited()
