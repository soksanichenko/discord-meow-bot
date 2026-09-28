"""Integration tests for exact-once stats operations and their migration."""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DataError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sources.lib.db.models import (
    Guild,
    MessageStats,
    StatsImportJob,
    StatsImportProgress,
)
from sources.lib.db.operations.stats import (
    apply_counts,
    delete_import_job,
    get_import_jobs,
    reset_guild_stats,
    upsert_import_job,
)

pytestmark = pytest.mark.integration

# Guild IDs 910_100-910_199 are reserved for this module.
_GUILD_ID = 910_100
_CHANNEL_ID = 1


@pytest.fixture
async def stats_ops(pg_async_url: str) -> AsyncGenerator[None, None]:
    """Point the stats operations at the test container."""
    engine = create_async_engine(pg_async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with patch('sources.lib.db.operations.stats.AsyncSession', factory):
        yield
    await engine.dispose()


async def _guild(db_session: AsyncSession, guild_id: int) -> None:
    await db_session.merge(Guild(id=guild_id, name='Stats'))
    await db_session.commit()


async def _progress(db_session: AsyncSession, guild_id: int) -> StatsImportProgress:
    db_session.expire_all()
    return (
        await db_session.execute(
            select(StatsImportProgress).where(
                StatsImportProgress.guild_id == guild_id,
                StatsImportProgress.channel_id == _CHANNEL_ID,
            )
        )
    ).scalar_one()


async def _count(db_session: AsyncSession, guild_id: int, user_id: int) -> int | None:
    db_session.expire_all()
    return await db_session.scalar(
        select(MessageStats.message_count).where(
            MessageStats.guild_id == guild_id, MessageStats.user_id == user_id
        )
    )


class TestApplyCounts:
    async def test_creates_range_at_range_start_and_counts(
        self, db_session: AsyncSession, stats_ops: None
    ) -> None:
        guild_id = _GUILD_ID + 1
        await _guild(db_session, guild_id)

        await apply_counts(
            guild_id, _CHANNEL_ID, {7: 3}, range_start=100, newest_id=150
        )

        row = await _progress(db_session, guild_id)
        assert (row.oldest_id, row.newest_id, row.is_completed) == (100, 150, False)
        assert await _count(db_session, guild_id, 7) == 3

    async def test_range_only_grows(
        self, db_session: AsyncSession, stats_ops: None
    ) -> None:
        guild_id = _GUILD_ID + 2
        await _guild(db_session, guild_id)
        await apply_counts(guild_id, _CHANNEL_ID, {}, range_start=100)

        await apply_counts(guild_id, _CHANNEL_ID, {}, range_start=0, newest_id=200)
        await apply_counts(guild_id, _CHANNEL_ID, {}, range_start=0, newest_id=150)
        await apply_counts(guild_id, _CHANNEL_ID, {}, range_start=0, oldest_id=50)
        await apply_counts(guild_id, _CHANNEL_ID, {}, range_start=0, oldest_id=80)

        row = await _progress(db_session, guild_id)
        assert (row.oldest_id, row.newest_id) == (50, 200)

    async def test_marks_completed(
        self, db_session: AsyncSession, stats_ops: None
    ) -> None:
        guild_id = _GUILD_ID + 3
        await _guild(db_session, guild_id)
        await apply_counts(guild_id, _CHANNEL_ID, {}, range_start=100)

        await apply_counts(
            guild_id, _CHANNEL_ID, {}, range_start=0, oldest_id=10, is_completed=True
        )

        assert (await _progress(db_session, guild_id)).is_completed is True

    async def test_failure_writes_neither_counts_nor_range(
        self, db_session: AsyncSession, stats_ops: None
    ) -> None:
        guild_id = _GUILD_ID + 4
        await _guild(db_session, guild_id)

        # The second user ID overflows BIGINT, so the transaction fails after the
        # first increment has already been executed.
        with pytest.raises(DataError):
            await apply_counts(
                guild_id, _CHANNEL_ID, {7: 1, 2**63: 1}, range_start=100, newest_id=150
            )

        assert await _count(db_session, guild_id, 7) is None
        db_session.expire_all()
        assert (
            await db_session.get(StatsImportProgress, (guild_id, _CHANNEL_ID))
        ) is None


class TestImportJobs:
    async def test_upsert_get_delete(
        self, db_session: AsyncSession, stats_ops: None
    ) -> None:
        guild_id = _GUILD_ID + 10
        await _guild(db_session, guild_id)
        since = datetime(2024, 1, 1, tzinfo=UTC)

        await upsert_import_job(guild_id, None)
        await upsert_import_job(guild_id, since)
        jobs = {job.guild_id: job.since for job in await get_import_jobs()}
        assert jobs[guild_id] == since

        await delete_import_job(guild_id)
        assert guild_id not in {job.guild_id for job in await get_import_jobs()}


class TestResetGuildStats:
    async def test_wipes_guild_and_enqueues_full_import(
        self, db_session: AsyncSession, stats_ops: None
    ) -> None:
        guild_id, other_id = _GUILD_ID + 20, _GUILD_ID + 21
        await _guild(db_session, guild_id)
        await _guild(db_session, other_id)
        await apply_counts(guild_id, _CHANNEL_ID, {7: 3}, range_start=100)
        await apply_counts(other_id, _CHANNEL_ID, {7: 5}, range_start=100)
        await upsert_import_job(guild_id, datetime(2024, 1, 1, tzinfo=UTC))

        await reset_guild_stats(guild_id)

        assert await _count(db_session, guild_id, 7) is None
        assert await _count(db_session, other_id, 7) == 5
        db_session.expire_all()
        assert (
            await db_session.get(StatsImportProgress, (guild_id, _CHANNEL_ID))
        ) is None
        job = await db_session.get(StatsImportJob, guild_id)
        assert job is not None and job.since is None


def _alembic(url: str, *args: str) -> None:
    result = subprocess.run(
        ['python', '-m', 'alembic', '-c', 'sources/alembic.ini', *args],
        env={**os.environ, 'TEST_ALEMBIC_URL': url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


class TestMigration:
    async def test_upgrade_wipes_stats_and_enqueues_reimport(
        self, pg_async_url: str, db_session: AsyncSession
    ) -> None:
        guild_id = _GUILD_ID + 30
        _alembic(pg_async_url, 'downgrade', 'bc25943c8c1d')
        try:
            await db_session.execute(
                text(
                    "INSERT INTO guilds (id, name) VALUES (:g, 'M') ON CONFLICT DO NOTHING"
                ),
                {'g': guild_id},
            )
            await db_session.execute(
                text(
                    'INSERT INTO message_stats (guild_id, user_id, message_count) '
                    'VALUES (:g, 7, 42)'
                ),
                {'g': guild_id},
            )
            await db_session.execute(
                text(
                    'INSERT INTO stats_import_progress '
                    '(guild_id, channel_id, last_message_id, is_completed) '
                    'VALUES (:g, 1, 123, true)'
                ),
                {'g': guild_id},
            )
            await db_session.commit()
        finally:
            _alembic(pg_async_url, 'upgrade', 'head')

        assert await _count(db_session, guild_id, 7) is None
        db_session.expire_all()
        assert (await db_session.get(StatsImportProgress, (guild_id, 1))) is None
        job = await db_session.get(StatsImportJob, guild_id)
        assert job is not None and job.since is None
