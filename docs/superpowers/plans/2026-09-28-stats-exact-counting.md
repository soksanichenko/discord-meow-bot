# Exact-Once Message Statistics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Count every non-bot guild message in `message_stats` exactly once across restarts, reconnects and history imports, and let admins reset and rebuild a guild's statistics.

**Architecture:** Each channel has a persisted counted range `[oldest_id, newest_id]`. The live counter and the downtime catch-up extend it upwards, the history import extends it downwards, and every extension writes counts and the new bound in one transaction (`apply_counts`). A new `StatsTracker` class owns the in-memory state (boundaries, floors, buffer, tasks). `StatsCog` becomes a thin layer of commands and listeners.

**Tech Stack:** Python 3.12, discord.py 2.7.1, SQLAlchemy 2.1 async + psycopg3, Alembic, pytest + pytest-asyncio (auto mode), testcontainers Postgres.

**Spec:** `docs/superpowers/specs/2026-09-28-stats-exact-counting-design.md`

## Global Constraints

- Single quotes for strings; triple double quotes for docstrings; Google-style docstrings on every public function, method and class.
- Logging via `Logger()` from `sources.lib.utils.logger` with %-formatting, never f-strings in log calls.
- Line length 88.
- Commit messages: Conventional Commits, English, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Admin commands carry both `@app_commands.default_permissions(manage_guild=True)` and `@app_commands.checks.has_permissions(manage_guild=True)` (enforced by `tests/cogs/test_permissions.py`).
- Run unit tests with dummy DB env so they can never reach the real database:
  `DB_HOST=127.0.0.1 DB_PORT=1 DB_LOGIN=x DB_PASSWORD=x DB_DATABASE=x DISCORD_TOKEN=x python -m pytest <path> -q -p no:cacheprovider`
  (referred to below as `$PYTEST`). Integration tests (`-m integration`) start a Postgres container through testcontainers and need Docker.
- Never run `alembic upgrade` against the real database without explicit user confirmation (Task 5).

## Review Focus

1. The bot is removed from a guild while a catch-up or import for it is running → its tasks stop and nothing is written for that guild afterwards (test in Task 3: `test_forget_stops_tasks_and_writes_nothing`).
2. A tracked channel was deleted while the bot was down → catch-up skips its gap instead of failing forever, and the channel's live counts still flush (test in Task 2: `test_deleted_channel_is_caught_up_without_history`).
3. The bot lost access to a tracked channel → same as 2 (test in Task 2: `test_forbidden_channel_is_caught_up_without_history`).
4. The database is briefly unavailable during catch-up → the step is retried and counts stay exact (test in Task 2: `test_catch_up_retries_after_db_error`).
5. A message sent just before the boundary is delivered by the gateway after `on_ready` → counted once, by catch-up (covered in Task 2: `test_restart_counts_downtime_and_late_messages_once`).

---

## File Structure

| File | Responsibility |
|---|---|
| `sources/lib/db/models.py` (modify) | `StatsImportProgress` gets `oldest_id`/`newest_id`; new `StatsImportJob` |
| `sources/lib/db/alembic/versions/3c9e1f7a2b4d_exact_once_message_stats.py` (create) | Schema change, wipe, enqueue re-import |
| `sources/lib/db/operations/stats.py` (modify) | `apply_counts`, job CRUD, `reset_guild_stats`; old write helpers removed in Task 4 |
| `sources/lib/stats_tracker.py` (create) | `StatsTracker`: live buffer, flush, boundaries, catch-up, import, reset, forget |
| `sources/lib/cogs/stats.py` (modify) | Commands, listeners and the flush loop, delegating to the tracker |
| `tests/db/test_stats_integration.py` (create) | `apply_counts`, jobs, reset and migration against real Postgres |
| `tests/cogs/_stats_fakes.py` (create) | In-memory fake DB operations, fake guild/channel/messages, clock |
| `tests/cogs/test_stats_tracker.py` (create) | Exact-once scenarios |
| `tests/cogs/test_stats.py` (rewrite) | Cog command tests |
| `tests/db/test_db_operations.py`, `tests/db/test_db_integration.py` (modify) | Drop tests of removed functions; add new table to the schema test |
| `CLAUDE.md`, `README.md` (modify) | Models, commands, structure |

---

### Task 1: Database layer

**Files:**
- Modify: `sources/lib/db/models.py:364-377`
- Create: `sources/lib/db/alembic/versions/3c9e1f7a2b4d_exact_once_message_stats.py`
- Modify: `sources/lib/db/operations/stats.py`
- Modify: `tests/db/test_db_integration.py:60-75` (schema table list)
- Create: `tests/db/test_stats_integration.py`

**Interfaces:**
- Produces (`sources.lib.db.operations.stats`):
  - `async def apply_counts(guild_id: int, channel_id: int, counts: dict[int, int], *, range_start: int, newest_id: int | None = None, oldest_id: int | None = None, is_completed: bool | None = None) -> None`
  - `async def upsert_import_job(guild_id: int, since: datetime | None) -> None`
  - `async def get_import_jobs() -> list[StatsImportJob]`
  - `async def delete_import_job(guild_id: int) -> None`
  - `async def reset_guild_stats(guild_id: int) -> None`
  - Unchanged: `get_leaderboard`, `get_channel_progress(guild_id, channel_id) -> StatsImportProgress | None`, `get_all_channel_progress(guild_id) -> list[StatsImportProgress]`
- Produces (`sources.lib.db.models`): `StatsImportProgress(guild_id, channel_id, oldest_id: int, newest_id: int, is_completed: bool)`, `StatsImportJob(guild_id, since: datetime | None)`

- [ ] **Step 1: Write the failing integration tests**

Create `tests/db/test_stats_integration.py`:

```python
"""Integration tests for exact-once stats operations and their migration."""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
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
        with pytest.raises(Exception):
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
```

Also add `'stats_import_jobs'` to the expected table list in `tests/db/test_db_integration.py` (`TestSchema.test_all_tables_exist`, next to `'stats_import_progress'`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$PYTEST tests/db/test_stats_integration.py -m integration`
Expected: collection error `ImportError: cannot import name 'StatsImportJob'`.

- [ ] **Step 3: Update the models**

Replace the `StatsImportProgress` class in `sources/lib/db/models.py` and add `StatsImportJob` right after it:

```python
class StatsImportProgress(Base):
    """Per-channel range of messages already counted in message_stats.

    Invariant: message_stats holds exactly the non-bot messages of the channel
    with oldest_id <= id <= newest_id, each counted once. The bounds may be
    synthetic time-based snowflakes rather than real message IDs.
    """

    __tablename__ = 'stats_import_progress'

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('guilds.id', ondelete='CASCADE'),
        primary_key=True,
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    oldest_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    newest_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # The backward history import reached the start of the channel (or `since`).
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class StatsImportJob(Base):
    """A requested backward history import that has not finished yet."""

    __tablename__ = 'stats_import_jobs'

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('guilds.id', ondelete='CASCADE'),
        primary_key=True,
    )
    # NULL means the whole channel history.
    since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 4: Write the migration**

First confirm the revision ID is unused: `grep -rl 3c9e1f7a2b4d sources/lib/db/alembic/versions` must print nothing, and `alembic heads` (with dummy env) must print `bc25943c8c1d (head)`.

Create `sources/lib/db/alembic/versions/3c9e1f7a2b4d_exact_once_message_stats.py`:

```python
"""exact-once message stats

Revision ID: 3c9e1f7a2b4d
Revises: bc25943c8c1d
Create Date: 2026-09-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3c9e1f7a2b4d'
down_revision: str | None = 'bc25943c8c1d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'stats_import_jobs',
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('since', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('guild_id'),
    )
    # Existing counts overlap with the live counter and cannot be trusted, so they
    # are wiped and rebuilt from history by a queued import.
    op.execute(
        'INSERT INTO stats_import_jobs (guild_id) '
        'SELECT guild_id FROM message_stats '
        'UNION SELECT guild_id FROM stats_import_progress'
    )
    op.execute('DELETE FROM message_stats')
    op.execute('DELETE FROM stats_import_progress')
    op.alter_column(
        'stats_import_progress',
        'last_message_id',
        new_column_name='oldest_id',
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.add_column(
        'stats_import_progress',
        sa.Column('newest_id', sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('stats_import_progress', 'newest_id')
    op.alter_column(
        'stats_import_progress',
        'oldest_id',
        new_column_name='last_message_id',
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.drop_table('stats_import_jobs')
```

- [ ] **Step 5: Add the operations**

In `sources/lib/db/operations/stats.py`: update the module docstring and imports, and append the new functions. Keep `increment_message_counts`, `save_channel_progress` and `get_guilds_with_incomplete_import` for now (the current cog still imports them; Task 4 removes them).

```python
"""Operations with DB tables `message_stats`, `stats_import_progress`, `stats_import_jobs`"""

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sources.lib.db import AsyncSession
from sources.lib.db.models import MessageStats, StatsImportJob, StatsImportProgress
```

```python
async def apply_counts(
    guild_id: int,
    channel_id: int,
    counts: dict[int, int],
    *,
    range_start: int,
    newest_id: int | None = None,
    oldest_id: int | None = None,
    is_completed: bool | None = None,
) -> None:
    """Add message counts and extend the channel's counted range in one transaction.

    Writing both together is what makes counting exact-once: after a crash the
    counts and the range have either both moved or neither has. The range only
    grows (GREATEST for newest_id, LEAST for oldest_id). A missing progress row is
    created as the empty range [range_start, range_start] before the bounds apply.

    Args:
        guild_id: Discord guild ID.
        channel_id: Discord channel ID.
        counts: Mapping of user_id to the number of messages to add.
        range_start: Bound for a progress row that does not exist yet.
        newest_id: New upper bound of the counted range, if it moves.
        oldest_id: New lower bound of the counted range, if it moves.
        is_completed: New value of the history-import-finished flag, if it changes.
    """
    async with AsyncSession() as session:
        for user_id, delta in counts.items():
            await session.execute(
                pg_insert(MessageStats)
                .values(guild_id=guild_id, user_id=user_id, message_count=delta)
                .on_conflict_do_update(
                    index_elements=['guild_id', 'user_id'],
                    set_={'message_count': MessageStats.message_count + delta},
                )
            )
        insert = pg_insert(StatsImportProgress).values(
            guild_id=guild_id,
            channel_id=channel_id,
            oldest_id=range_start if oldest_id is None else min(range_start, oldest_id),
            newest_id=range_start if newest_id is None else max(range_start, newest_id),
            is_completed=bool(is_completed),
        )
        updates = {}
        if newest_id is not None:
            updates['newest_id'] = func.greatest(
                StatsImportProgress.newest_id, newest_id
            )
        if oldest_id is not None:
            updates['oldest_id'] = func.least(StatsImportProgress.oldest_id, oldest_id)
        if is_completed is not None:
            updates['is_completed'] = is_completed
        index_elements = ['guild_id', 'channel_id']
        if updates:
            stmt = insert.on_conflict_do_update(
                index_elements=index_elements, set_=updates
            )
        else:
            stmt = insert.on_conflict_do_nothing(index_elements=index_elements)
        await session.execute(stmt)
        await session.commit()


async def upsert_import_job(guild_id: int, since: datetime | None) -> None:
    """Request a history import for a guild, replacing any pending request.

    Args:
        guild_id: Discord guild ID.
        since: Lower time bound for the import; None imports the whole history.
    """
    async with AsyncSession() as session:
        await session.execute(
            pg_insert(StatsImportJob)
            .values(guild_id=guild_id, since=since)
            .on_conflict_do_update(index_elements=['guild_id'], set_={'since': since})
        )
        await session.commit()


async def get_import_jobs() -> list[StatsImportJob]:
    """Return all pending history import requests.

    Returns:
        List of StatsImportJob rows.
    """
    async with AsyncSession() as session:
        result = await session.scalars(select(StatsImportJob))
        return list(result.all())


async def delete_import_job(guild_id: int) -> None:
    """Mark a guild's history import as finished.

    Args:
        guild_id: Discord guild ID.
    """
    async with AsyncSession() as session:
        await session.execute(
            delete(StatsImportJob).where(StatsImportJob.guild_id == guild_id)
        )
        await session.commit()


async def reset_guild_stats(guild_id: int) -> None:
    """Wipe a guild's statistics and request a full re-import, in one transaction.

    Args:
        guild_id: Discord guild ID.
    """
    async with AsyncSession() as session:
        await session.execute(
            delete(MessageStats).where(MessageStats.guild_id == guild_id)
        )
        await session.execute(
            delete(StatsImportProgress).where(StatsImportProgress.guild_id == guild_id)
        )
        await session.execute(
            pg_insert(StatsImportJob)
            .values(guild_id=guild_id, since=None)
            .on_conflict_do_update(index_elements=['guild_id'], set_={'since': None})
        )
        await session.commit()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `$PYTEST tests/db/test_stats_integration.py tests/db/test_db_integration.py -m integration`
Expected: all PASS.

Run: `$PYTEST tests/cogs tests/db -m "not integration"`
Expected: all PASS (the current cog is untouched at import level).

- [ ] **Step 7: Commit**

```bash
git add sources/lib/db/models.py sources/lib/db/operations/stats.py \
  sources/lib/db/alembic/versions/3c9e1f7a2b4d_exact_once_message_stats.py \
  tests/db/test_stats_integration.py tests/db/test_db_integration.py
git commit -m "feat(stats): add counted-range schema and atomic apply_counts"
```

---

### Task 2: StatsTracker — live counting, flush, epochs and catch-up

**Files:**
- Create: `sources/lib/stats_tracker.py`
- Create: `tests/cogs/_stats_fakes.py`
- Create: `tests/cogs/test_stats_tracker.py`

**Interfaces:**
- Consumes: `apply_counts`, `get_channel_progress`, `get_all_channel_progress` from Task 1; `upsert_guild(guild_id: int, guild_name: str)` from `sources.lib.db.operations.guilds`.
- Produces (`sources.lib.stats_tracker`):
  - `def now_snowflake() -> int`
  - module constants `_CHECKPOINT_EVERY = 500`, `_RETRY_DELAY_SECONDS = 60` (tests patch them)
  - `class StatsTracker` with `record(message) -> None`, `async flush() -> None`, `async on_ready(guilds: Sequence[discord.Guild]) -> None`, `on_guild_join(guild) -> None`, `catching_up_count(guild_id: int) -> int`, `async close() -> None`
  - private attributes used by the test helpers: `_catch_up_tasks: dict[int, asyncio.Task]`, `_import_tasks: dict[int, asyncio.Task]`

- [ ] **Step 1: Write the fakes**

Create `tests/cogs/_stats_fakes.py`:

```python
"""In-memory fakes for StatsTracker tests: DB operations, Discord objects, clock."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import discord
import pytest
from sqlalchemy.exc import OperationalError

BASE = datetime(2024, 1, 1, tzinfo=UTC)
GUILD_ID = 1


def sf(minute: float) -> int:
    """Return the lowest snowflake for BASE + minute."""
    return discord.utils.time_snowflake(BASE + timedelta(minutes=minute))


@dataclass
class FakeMessage:
    id: int
    author_id: int
    bot: bool = False

    @property
    def author(self) -> SimpleNamespace:
        return SimpleNamespace(id=self.author_id, bot=self.bot)


class FakeChannel:
    """A channel whose history() replays its message list like discord.py does."""

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.name = f'ch{channel_id}'
        self.messages: list[FakeMessage] = []
        self.forbidden = False
        self._pause_after: int | None = None
        self.paused = asyncio.Event()
        self._resume = asyncio.Event()

    def pause(self, after: int) -> None:
        """Make the next history() block after yielding `after` messages."""
        self._pause_after = after
        self.paused = asyncio.Event()
        self._resume = asyncio.Event()

    def unpause(self) -> None:
        self._pause_after = None
        self._resume.set()

    def permissions_for(self, member: object) -> SimpleNamespace:
        return SimpleNamespace(read_message_history=True)

    async def history(
        self,
        *,
        limit: int | None = None,
        after: discord.Object | None = None,
        before: discord.Object | None = None,
        oldest_first: bool = False,
    ):
        if self.forbidden:
            raise discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), '')
        low = after.id if after else -1
        high = before.id if before else 1 << 64
        selected = sorted(
            (m for m in self.messages if low < m.id < high),
            key=lambda m: m.id,
            reverse=not oldest_first,
        )
        for index, message in enumerate(selected):
            if index == self._pause_after:
                self.paused.set()
                await self._resume.wait()
            yield message


class FakeGuild:
    def __init__(self, channels: Iterable[FakeChannel]) -> None:
        self.id = GUILD_ID
        self.name = 'guild'
        self.me = object()
        self.text_channels = list(channels)

    def get_channel_or_thread(self, channel_id: int) -> FakeChannel | None:
        return next((c for c in self.text_channels if c.id == channel_id), None)


class FakeStatsDB:
    """Mirrors sources.lib.db.operations.stats, including atomic apply_counts."""

    def __init__(self) -> None:
        self.counts: Counter[tuple[int, int]] = Counter()
        self.progress: dict[tuple[int, int], SimpleNamespace] = {}
        self.jobs: dict[int, datetime | None] = {}
        self.writes = 0
        # Exceptions raised by the next apply_counts calls, in order.
        self.failures: list[Exception] = []

    async def apply_counts(
        self,
        guild_id: int,
        channel_id: int,
        counts: dict[int, int],
        *,
        range_start: int,
        newest_id: int | None = None,
        oldest_id: int | None = None,
        is_completed: bool | None = None,
    ) -> None:
        # Yield first so a cancellation lands before the (atomic) mutation.
        await asyncio.sleep(0)
        if self.failures:
            raise self.failures.pop(0)
        self.writes += 1
        for user_id, delta in counts.items():
            self.counts[(guild_id, user_id)] += delta
        key = (guild_id, channel_id)
        row = self.progress.get(key)
        if row is None:
            row = self.progress[key] = SimpleNamespace(
                guild_id=guild_id,
                channel_id=channel_id,
                oldest_id=range_start,
                newest_id=range_start,
                is_completed=False,
            )
        if newest_id is not None:
            row.newest_id = max(row.newest_id, newest_id)
        if oldest_id is not None:
            row.oldest_id = min(row.oldest_id, oldest_id)
        if is_completed is not None:
            row.is_completed = is_completed

    async def get_channel_progress(
        self, guild_id: int, channel_id: int
    ) -> SimpleNamespace | None:
        row = self.progress.get((guild_id, channel_id))
        return SimpleNamespace(**vars(row)) if row else None

    async def get_all_channel_progress(self, guild_id: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(**vars(row))
            for (g, _), row in self.progress.items()
            if g == guild_id
        ]

    async def get_import_jobs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(guild_id=g, since=s) for g, s in self.jobs.items()]

    async def upsert_import_job(self, guild_id: int, since: datetime | None) -> None:
        self.jobs[guild_id] = since

    async def delete_import_job(self, guild_id: int) -> None:
        self.jobs.pop(guild_id, None)

    async def reset_guild_stats(self, guild_id: int) -> None:
        await asyncio.sleep(0)
        for key in [k for k in self.counts if k[0] == guild_id]:
            del self.counts[key]
        for key in [k for k in self.progress if k[0] == guild_id]:
            del self.progress[key]
        self.jobs[guild_id] = None

    async def upsert_guild(self, guild_id: int, guild_name: str) -> None:
        pass

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in (
            'apply_counts',
            'get_channel_progress',
            'get_all_channel_progress',
            'get_import_jobs',
            'delete_import_job',
            'reset_guild_stats',
            'upsert_guild',
        ):
            monkeypatch.setattr(
                f'sources.lib.stats_tracker.{name}', getattr(self, name)
            )

    def user_counts(self) -> dict[int, int]:
        return {u: n for (g, u), n in self.counts.items() if g == GUILD_ID and n}


def db_error() -> OperationalError:
    return OperationalError('stmt', {}, Exception('db down'))


class Clock:
    def __init__(self) -> None:
        self.minute = 0.0

    def __call__(self) -> int:
        return sf(self.minute)


class World:
    """One guild whose channels hold the full message history, plus a fake DB."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, channel_ids=(10,)) -> None:
        self.db = FakeStatsDB()
        self.db.install(monkeypatch)
        self.clock = Clock()
        monkeypatch.setattr('sources.lib.stats_tracker.now_snowflake', self.clock)
        monkeypatch.setattr('sources.lib.stats_tracker._RETRY_DELAY_SECONDS', 0)
        self.channels = {cid: FakeChannel(cid) for cid in channel_ids}
        self.guild = FakeGuild(self.channels.values())

    def send(
        self,
        tracker,
        channel_id: int,
        minute: float,
        author_id: int,
        *,
        bot: bool = False,
    ) -> None:
        """Post a message; `tracker=None` means the bot is offline and misses it."""
        message = FakeMessage(sf(minute) + 1, author_id, bot)
        channel = self.channels[channel_id]
        channel.messages.append(message)
        if tracker is not None:
            tracker.record(live(self.guild, channel, message))

    def expected(self, keep: Callable[[FakeMessage], bool] = lambda m: True):
        counts = Counter(
            m.author_id
            for channel in self.channels.values()
            for m in channel.messages
            if not m.bot and keep(m)
        )
        return dict(counts)


def live(
    guild: FakeGuild, channel: FakeChannel, message: FakeMessage
) -> SimpleNamespace:
    """Shape a message the way on_message delivers it."""
    return SimpleNamespace(
        id=message.id,
        author=message.author,
        guild=SimpleNamespace(id=guild.id),
        channel=SimpleNamespace(id=channel.id),
    )


def _tasks(tracker) -> list[asyncio.Task]:
    return [*tracker._catch_up_tasks.values(), *tracker._import_tasks.values()]


async def settle(tracker) -> None:
    """Wait until all catch-up and import tasks have finished."""
    while pending := [t for t in _tasks(tracker) if not t.done()]:
        await asyncio.gather(*pending)


async def crash(tracker) -> None:
    """Kill the tracker's background work without flushing, like a dead process."""
    for task in _tasks(tracker):
        task.cancel()
    await asyncio.gather(*_tasks(tracker), return_exceptions=True)
```

- [ ] **Step 2: Write the failing scenario tests**

Create `tests/cogs/test_stats_tracker.py`:

```python
"""Exact-once scenarios for StatsTracker, run against in-memory fakes."""

from __future__ import annotations

import pytest

from sources.lib.stats_tracker import StatsTracker
from tests.cogs._stats_fakes import GUILD_ID, World, crash, db_error, settle, sf


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


@pytest.fixture
def small_checkpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('sources.lib.stats_tracker._CHECKPOINT_EVERY', 2)


async def _first_run(world: World) -> None:
    """A previous process that counted minutes 1-3 live and then died."""
    first = StatsTracker()
    await first.on_ready([world.guild])
    for minute in (1, 2, 3):
        world.send(first, 10, minute, author_id=minute % 2)
    world.send(first, 10, 3.5, author_id=9, bot=True)
    await first.flush()
    await crash(first)


class TestFlush:
    async def test_live_messages_are_counted(self, world: World) -> None:
        tracker = StatsTracker()
        await tracker.on_ready([world.guild])
        world.send(tracker, 10, 1, author_id=5)
        world.send(tracker, 10, 2, author_id=5)
        world.send(tracker, 10, 3, author_id=6, bot=True)

        await tracker.flush()

        assert world.db.user_counts() == {5: 2}

    async def test_db_outage_keeps_counts_for_next_flush(self, world: World) -> None:
        tracker = StatsTracker()
        await tracker.on_ready([world.guild])
        world.send(tracker, 10, 1, author_id=5)
        world.db.failures.append(db_error())

        await tracker.flush()
        world.send(tracker, 10, 2, author_id=5)
        await tracker.flush()

        assert world.db.user_counts() == {5: 2}

    async def test_rejected_counts_are_dropped(self, world: World) -> None:
        tracker = StatsTracker()
        await tracker.on_ready([world.guild])
        world.send(tracker, 10, 1, author_id=5)
        world.db.failures.append(ValueError('rejected'))

        await tracker.flush()
        await tracker.flush()

        assert world.db.user_counts() == {}


class TestRestart:
    async def test_restart_counts_downtime_and_late_messages_once(
        self, world: World
    ) -> None:
        await _first_run(world)
        for minute in range(4, 10):
            world.send(None, 10, minute, author_id=minute % 3)

        second = StatsTracker()
        # Delivered before on_ready: ignored live, read back by catch-up.
        world.send(second, 10, 14, author_id=4)
        world.clock.minute = 20
        await second.on_ready([world.guild])
        # Sent before the boundary but delivered after on_ready.
        world.send(second, 10, 19.5, author_id=4)
        world.send(second, 10, 21, author_id=4)
        await settle(second)
        await second.flush()

        assert world.db.user_counts() == world.expected()

    async def test_crash_mid_catch_up_then_restart(
        self, world: World, small_checkpoints: None
    ) -> None:
        await _first_run(world)
        for minute in range(4, 14):
            world.send(None, 10, minute, author_id=minute % 3)
        second = StatsTracker()
        world.clock.minute = 20
        world.channels[10].pause(after=5)
        await second.on_ready([world.guild])
        await world.channels[10].paused.wait()

        await crash(second)
        world.channels[10].unpause()
        third = StatsTracker()
        world.clock.minute = 30
        await third.on_ready([world.guild])
        await settle(third)
        await third.flush()

        assert world.db.user_counts() == world.expected()

    async def test_crash_with_held_live_counts_then_restart(self, world: World) -> None:
        await _first_run(world)
        for minute in range(4, 8):
            world.send(None, 10, minute, author_id=1)
        second = StatsTracker()
        world.clock.minute = 20
        world.channels[10].pause(after=1)
        await second.on_ready([world.guild])
        await world.channels[10].paused.wait()
        world.send(second, 10, 21, author_id=7)
        await second.flush()

        # Held until the channel is caught up.
        assert second.catching_up_count(GUILD_ID) == 1
        assert 7 not in world.db.user_counts()

        await crash(second)
        world.channels[10].unpause()
        third = StatsTracker()
        world.clock.minute = 30
        await third.on_ready([world.guild])
        await settle(third)
        await third.flush()

        assert world.db.user_counts() == world.expected()

    async def test_reconnect_during_catch_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = World(monkeypatch, channel_ids=(10, 11))
        first = StatsTracker()
        await first.on_ready([world.guild])
        world.send(first, 10, 1, author_id=1)
        world.send(first, 11, 1, author_id=2)
        await first.flush()
        await crash(first)
        for minute in range(2, 6):
            world.send(None, 10, minute, author_id=1)
            world.send(None, 11, minute, author_id=2)

        second = StatsTracker()
        world.clock.minute = 20
        world.channels[11].pause(after=2)
        await second.on_ready([world.guild])
        await world.channels[11].paused.wait()
        # Channel 10 is caught up, channel 11 is not; neither buffer is flushed.
        world.send(second, 10, 21, author_id=3)
        world.send(second, 11, 21, author_id=4)
        # Missed during the gateway disconnect.
        world.send(None, 10, 25, author_id=5)
        world.send(None, 11, 25, author_id=6)

        world.channels[11].unpause()
        world.clock.minute = 30
        await second.on_ready([world.guild])
        await settle(second)
        await second.flush()

        assert world.db.user_counts() == world.expected()


class TestCatchUpEdgeCases:
    async def test_catch_up_retries_after_db_error(
        self, world: World, small_checkpoints: None
    ) -> None:
        await _first_run(world)
        for minute in range(4, 10):
            world.send(None, 10, minute, author_id=2)
        second = StatsTracker()
        world.clock.minute = 20
        world.db.failures.append(db_error())

        await second.on_ready([world.guild])
        await settle(second)
        await second.flush()

        assert world.db.user_counts() == world.expected()

    async def test_deleted_channel_is_caught_up_without_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = World(monkeypatch, channel_ids=(10, 11))
        first = StatsTracker()
        await first.on_ready([world.guild])
        await crash(first)
        world.guild.text_channels.remove(world.channels[11])

        second = StatsTracker()
        world.clock.minute = 20
        await second.on_ready([world.guild])
        await settle(second)

        assert second.catching_up_count(GUILD_ID) == 0
        assert world.db.progress[(GUILD_ID, 11)].newest_id == sf(20)

    async def test_forbidden_channel_is_caught_up_without_history(
        self, world: World
    ) -> None:
        await _first_run(world)
        world.send(None, 10, 5, author_id=1)
        world.channels[10].forbidden = True
        second = StatsTracker()
        world.clock.minute = 20

        await second.on_ready([world.guild])
        await settle(second)
        world.send(second, 10, 21, author_id=8)
        await second.flush()

        assert second.catching_up_count(GUILD_ID) == 0
        assert world.db.user_counts()[8] == 1
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `$PYTEST tests/cogs/test_stats_tracker.py`
Expected: collection error `ModuleNotFoundError: No module named 'sources.lib.stats_tracker'`.

- [ ] **Step 4: Implement the tracker**

Create `sources/lib/stats_tracker.py`:

```python
"""Exact-once message counting for /stats.

Each channel has a persisted range [oldest_id, newest_id] of messages already
counted (see StatsImportProgress). Three writers extend it, each moving the range
together with the counts in one transaction:

- the live counter (on_message, flushed periodically) moves newest_id up;
- catch-up after downtime reads history forward from newest_id to the boundary;
- the history import reads history backward from oldest_id.

The boundary is the snowflake from which the live counter owns a guild's
messages. Messages at or below a channel's floor are left to catch-up or import.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import discord
from sqlalchemy.exc import OperationalError

from sources.lib.db.operations.guilds import upsert_guild
from sources.lib.db.operations.stats import (
    apply_counts,
    get_all_channel_progress,
    get_channel_progress,
)
from sources.lib.utils.logger import Logger

_CHECKPOINT_EVERY = 500
_RETRY_DELAY_SECONDS = 60

_ChannelKey = tuple[int, int]  # (guild_id, channel_id)


def now_snowflake() -> int:
    """Return the lowest snowflake for the current moment.

    Returns:
        A time-based snowflake usable as a message ID bound.
    """
    return discord.utils.time_snowflake(datetime.now(UTC))


@dataclass
class _Pending:
    """Live counts for one channel that are not in the database yet."""

    floor: int
    counts: defaultdict[int, int] = field(default_factory=lambda: defaultdict(int))
    max_id: int = 0


class StatsTracker:
    """Counts every non-bot guild message exactly once across restarts."""

    def __init__(self) -> None:
        """Start with no boundaries; nothing is counted live before on_ready."""
        self.logger = Logger()
        self._lock = asyncio.Lock()
        self._boundary: dict[int, int] = {}
        self._floor: dict[_ChannelKey, int] = {}
        self._caught_up: set[_ChannelKey] = set()
        self._buffer: dict[_ChannelKey, _Pending] = {}
        self._catch_up_tasks: dict[int, asyncio.Task] = {}
        self._import_tasks: dict[int, asyncio.Task] = {}

    def record(self, message: discord.Message) -> None:
        """Buffer a live message unless catch-up or import owns it.

        Args:
            message: The message from on_message.
        """
        if message.author.bot or message.guild is None:
            return
        guild_id = message.guild.id
        boundary = self._boundary.get(guild_id)
        if boundary is None:
            # Before the first on_ready; catch-up reads these from history.
            return
        key = (guild_id, message.channel.id)
        floor = self._floor.get(key)
        if floor is None:
            # No counted range yet (new channel, thread): the live counter owns
            # everything after the guild boundary.
            floor = self._floor[key] = boundary
            self._caught_up.add(key)
        if message.id <= floor:
            return
        pending = self._buffer.get(key)
        if pending is None:
            pending = self._buffer[key] = _Pending(floor=floor)
        pending.counts[message.author.id] += 1
        pending.max_id = max(pending.max_id, message.id)

    async def flush(self) -> None:
        """Write buffered counts of caught-up channels; hold the rest in memory."""
        async with self._lock:
            ready = {k: p for k, p in self._buffer.items() if k in self._caught_up}
            for key in ready:
                del self._buffer[key]
            await self._write_pending(ready, retry=True)

    async def on_ready(self, guilds: Sequence[discord.Guild]) -> None:
        """Start a new counting epoch after connecting or fully reconnecting.

        Args:
            guilds: All guilds the bot is in.
        """
        async with self._lock:
            await self._stop_all(self._catch_up_tasks)
            # No await from here until the floors are reset, so no live message
            # can slip in between the buffer snapshot and the new boundary.
            boundary = now_snowflake()
            kept = {k: p for k, p in self._buffer.items() if k in self._caught_up}
            self._buffer = {}
            self._floor = {}
            self._caught_up = set()
            for guild in guilds:
                self._boundary[guild.id] = boundary
            # Buffers of channels that were not caught up are dropped: their
            # newest_id did not move, so the new catch-up counts those messages.
            await self._write_pending(kept, retry=False)
            for guild in guilds:
                await self._retrying(self._start_epoch, guild, boundary)

    def on_guild_join(self, guild: discord.Guild) -> None:
        """Start counting a newly joined guild live.

        Args:
            guild: The guild the bot joined.
        """
        self._boundary[guild.id] = now_snowflake()

    def catching_up_count(self, guild_id: int) -> int:
        """Return how many of a guild's channels are still catching up.

        Args:
            guild_id: Discord guild ID.

        Returns:
            Number of tracked channels that are not caught up.
        """
        return sum(
            1
            for key in self._floor
            if key[0] == guild_id and key not in self._caught_up
        )

    async def close(self) -> None:
        """Stop background work and flush what can be flushed."""
        await self._stop_all(self._catch_up_tasks)
        await self._stop_all(self._import_tasks)
        await self.flush()

    async def _start_epoch(self, guild: discord.Guild, boundary: int) -> None:
        # The guild row must exist before progress rows reference it.
        await upsert_guild(guild.id, guild.name)
        rows = {r.channel_id: r for r in await get_all_channel_progress(guild.id)}
        readable = {
            channel.id
            for channel in guild.text_channels
            if channel.permissions_for(guild.me).read_message_history
        }
        stale = []
        for channel_id in sorted(rows.keys() | readable):
            key = (guild.id, channel_id)
            self._floor[key] = boundary
            if channel_id in rows:
                # A live message may have marked it caught up during an await.
                self._caught_up.discard(key)
                stale.append(channel_id)
            else:
                await apply_counts(guild.id, channel_id, {}, range_start=boundary)
                self._caught_up.add(key)
        if stale:
            self._catch_up_tasks[guild.id] = asyncio.create_task(
                self._catch_up(guild, stale, boundary)
            )

    async def _catch_up(
        self, guild: discord.Guild, channel_ids: list[int], boundary: int
    ) -> None:
        for channel_id in channel_ids:
            await self._retrying(self._catch_up_channel, guild, channel_id, boundary)
            self._caught_up.add((guild.id, channel_id))

    async def _catch_up_channel(
        self, guild: discord.Guild, channel_id: int, boundary: int
    ) -> None:
        row = await get_channel_progress(guild.id, channel_id)
        channel = guild.get_channel_or_thread(channel_id)
        counts: defaultdict[int, int] = defaultdict(int)
        if channel is None:
            self.logger.warning(
                'Stats catch-up: channel %s is gone, skipping its gap', channel_id
            )
        else:
            processed = 0
            try:
                async for message in channel.history(
                    limit=None,
                    after=discord.Object(id=row.newest_id),
                    # +1 so a message whose ID equals the boundary is not lost.
                    before=discord.Object(id=boundary + 1),
                    oldest_first=True,
                ):
                    if not message.author.bot:
                        counts[message.author.id] += 1
                    processed += 1
                    if processed % _CHECKPOINT_EVERY == 0:
                        await self._write(
                            guild.id,
                            channel_id,
                            counts,
                            range_start=boundary,
                            newest_id=message.id,
                        )
                        counts = defaultdict(int)
            except discord.Forbidden:
                self.logger.warning(
                    'Stats catch-up: no access to #%s, skipping its gap', channel.name
                )
        await self._write(
            guild.id, channel_id, counts, range_start=boundary, newest_id=boundary
        )

    async def _write_pending(
        self, pending_by_key: dict[_ChannelKey, _Pending], *, retry: bool
    ) -> None:
        for key, pending in pending_by_key.items():
            guild_id, channel_id = key
            try:
                await apply_counts(
                    guild_id,
                    channel_id,
                    dict(pending.counts),
                    range_start=pending.floor,
                    newest_id=pending.max_id,
                )
            except OperationalError:
                self.logger.warning(
                    'Stats flush failed for channel %s', channel_id, exc_info=True
                )
                if retry:
                    self._merge_back(key, pending)
            except Exception:
                self.logger.exception(
                    'Stats flush: dropping counts for channel %s', channel_id
                )

    def _merge_back(self, key: _ChannelKey, pending: _Pending) -> None:
        current = self._buffer.get(key)
        if current is None:
            self._buffer[key] = pending
            return
        for user_id, count in pending.counts.items():
            current.counts[user_id] += count
        current.max_id = max(current.max_id, pending.max_id)
        current.floor = min(current.floor, pending.floor)

    async def _write(
        self, guild_id: int, channel_id: int, counts: dict[int, int], **bounds: object
    ) -> None:
        """Run apply_counts; if cancelled, wait for the in-flight write first.

        Otherwise a stopped task could commit after the next reader of the range
        (a new catch-up or import) has read it, and messages would count twice.
        """
        write = asyncio.ensure_future(
            apply_counts(guild_id, channel_id, dict(counts), **bounds)
        )
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            await asyncio.gather(write, return_exceptions=True)
            raise

    async def _retrying(
        self, step: Callable[..., Awaitable[None]], *args: object
    ) -> None:
        # Steps are atomic per checkpoint, so repeating one never double-counts.
        while True:
            try:
                await step(*args)
                return
            except Exception:
                self.logger.exception(
                    'Stats: %s failed, retrying in %d s',
                    step.__name__,
                    _RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(_RETRY_DELAY_SECONDS)

    @staticmethod
    async def _stop(task: asyncio.Task | None) -> None:
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _stop_all(self, tasks: dict[int, asyncio.Task]) -> None:
        for task in list(tasks.values()):
            await self._stop(task)
        tasks.clear()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `$PYTEST tests/cogs/test_stats_tracker.py -v`
Expected: all PASS. If a scenario fails, compare `world.db.user_counts()` with `world.expected()`: a surplus means double counting, a deficit means a gap. Fix the tracker, not the test.

- [ ] **Step 6: Commit**

```bash
git add sources/lib/stats_tracker.py tests/cogs/_stats_fakes.py tests/cogs/test_stats_tracker.py
git commit -m "feat(stats): add exact-once tracker with live counting and catch-up"
```

---

### Task 3: StatsTracker — history import, reset, forget

**Files:**
- Modify: `sources/lib/stats_tracker.py`
- Modify: `tests/cogs/test_stats_tracker.py`

**Interfaces:**
- Consumes: `get_import_jobs`, `delete_import_job`, `reset_guild_stats` from Task 1; tracker internals from Task 2.
- Produces: `StatsTracker.start_import(guild, since: datetime | None) -> bool`, `StatsTracker.is_importing(guild_id: int) -> bool`, `async StatsTracker.reset(guild) -> None`, `async StatsTracker.forget(guild_id: int) -> None`. `on_ready` also resumes pending jobs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/cogs/test_stats_tracker.py`:

```python
class TestImport:
    async def test_import_in_parallel_with_live_counting(
        self, world: World, small_checkpoints: None
    ) -> None:
        for minute in range(1, 10):
            world.send(None, 10, minute, author_id=minute % 2)
        tracker = StatsTracker()
        world.clock.minute = 10
        await tracker.on_ready([world.guild])
        await world.db.upsert_import_job(GUILD_ID, None)
        world.channels[10].pause(after=3)

        assert tracker.start_import(world.guild, None) is True
        await world.channels[10].paused.wait()
        assert tracker.start_import(world.guild, None) is False
        for minute in (11, 12, 13):
            world.send(tracker, 10, minute, author_id=7)
        await tracker.flush()
        world.channels[10].unpause()
        await settle(tracker)
        await tracker.flush()

        assert world.db.user_counts() == world.expected()
        assert world.db.jobs == {}
        assert world.db.progress[(GUILD_ID, 10)].is_completed is True

    async def test_import_resumes_after_restart_and_honours_since(
        self, world: World, small_checkpoints: None
    ) -> None:
        for minute in range(1, 21):
            world.send(None, 10, minute, author_id=minute % 3)
        since = BASE + timedelta(minutes=10)
        first = StatsTracker()
        world.clock.minute = 30
        await first.on_ready([world.guild])
        await world.db.upsert_import_job(GUILD_ID, since)
        world.channels[10].pause(after=3)
        first.start_import(world.guild, since)
        await world.channels[10].paused.wait()

        await crash(first)
        world.channels[10].unpause()
        second = StatsTracker()
        world.clock.minute = 40
        await second.on_ready([world.guild])
        await settle(second)

        assert world.db.user_counts() == world.expected(lambda m: m.id > sf(10))
        assert world.db.jobs == {}


class TestReset:
    async def test_reset_rebuilds_counts_exactly(self, world: World) -> None:
        for minute in range(1, 6):
            world.send(None, 10, minute, author_id=1)
        tracker = StatsTracker()
        world.clock.minute = 10
        await tracker.on_ready([world.guild])
        tracker.start_import(world.guild, None)
        await settle(tracker)
        world.send(tracker, 10, 11, author_id=2)
        await tracker.flush()
        world.send(tracker, 10, 12, author_id=3)  # still buffered at reset time

        world.clock.minute = 20
        await tracker.reset(world.guild)
        # Sent before the reset boundary, delivered after it.
        world.send(tracker, 10, 19.5, author_id=4)
        world.send(tracker, 10, 21, author_id=5)
        await settle(tracker)
        await tracker.flush()

        assert world.db.user_counts() == world.expected()
        assert world.db.jobs == {}


class TestForget:
    async def test_forget_stops_tasks_and_writes_nothing(self, world: World) -> None:
        for minute in range(1, 6):
            world.send(None, 10, minute, author_id=1)
        tracker = StatsTracker()
        world.clock.minute = 10
        await tracker.on_ready([world.guild])
        world.channels[10].pause(after=2)
        tracker.start_import(world.guild, None)
        await world.channels[10].paused.wait()
        world.send(tracker, 10, 11, author_id=2)
        writes = world.db.writes

        await tracker.forget(GUILD_ID)
        world.channels[10].unpause()
        await tracker.flush()

        assert tracker.is_importing(GUILD_ID) is False
        assert world.db.writes == writes
```

Add at the top of the test module:

```python
from datetime import timedelta

from tests.cogs._stats_fakes import BASE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$PYTEST tests/cogs/test_stats_tracker.py -k "Import or Reset or Forget"`
Expected: FAIL with `AttributeError: 'StatsTracker' object has no attribute 'start_import'`.

- [ ] **Step 3: Implement import, reset and forget**

In `sources/lib/stats_tracker.py`, extend the operations import:

```python
from sources.lib.db.operations.stats import (
    apply_counts,
    delete_import_job,
    get_all_channel_progress,
    get_channel_progress,
    get_import_jobs,
    reset_guild_stats,
)
```

At the end of `on_ready`, after the `async with self._lock:` block (outside it), resume jobs:

```python
        by_id = {guild.id: guild for guild in guilds}
        for job in await get_import_jobs():
            guild = by_id.get(job.guild_id)
            if guild is not None:
                self.start_import(guild, job.since)
```

Add these public methods after `on_guild_join`:

```python
def start_import(self, guild: discord.Guild, since: datetime | None) -> bool:
    """Start the backward history import for a guild unless one is running.

    Args:
        guild: The guild to import.
        since: Lower time bound; None imports the whole history.

    Returns:
        True if a new import was started.
    """
    if self.is_importing(guild.id):
        return False
    self._import_tasks[guild.id] = asyncio.create_task(self._import_guild(guild, since))
    return True


def is_importing(self, guild_id: int) -> bool:
    """Return whether a history import is running for a guild.

    Args:
        guild_id: Discord guild ID.

    Returns:
        True while the import task is running.
    """
    task = self._import_tasks.get(guild_id)
    return task is not None and not task.done()


async def reset(self, guild: discord.Guild) -> None:
    """Wipe a guild's statistics and rebuild them from history.

    Args:
        guild: The guild to reset.
    """
    async with self._lock:
        await self._stop(self._catch_up_tasks.pop(guild.id, None))
        await self._stop(self._import_tasks.pop(guild.id, None))
        self._boundary[guild.id] = now_snowflake()
        self._drop_guild_state(guild.id)
        await reset_guild_stats(guild.id)
    self.start_import(guild, since=None)


async def forget(self, guild_id: int) -> None:
    """Stop all work for a guild the bot has left.

    Args:
        guild_id: Discord guild ID.
    """
    async with self._lock:
        await self._stop(self._catch_up_tasks.pop(guild_id, None))
        await self._stop(self._import_tasks.pop(guild_id, None))
        self._boundary.pop(guild_id, None)
        self._drop_guild_state(guild_id)
```

Add these private methods after `_catch_up_channel`:

```python
async def _import_guild(self, guild: discord.Guild, since: datetime | None) -> None:
    after = discord.Object(id=discord.utils.time_snowflake(since)) if since else None
    channels = [
        channel
        for channel in guild.text_channels
        if channel.permissions_for(guild.me).read_message_history
    ]
    self.logger.info(
        'Stats import started for guild %s: %d channels', guild.name, len(channels)
    )
    for channel in channels:
        await self._retrying(self._import_channel, guild, channel, after)
    await self._retrying(delete_import_job, guild.id)
    self.logger.info('Stats import complete for guild %s', guild.name)


async def _import_channel(
    self,
    guild: discord.Guild,
    channel: discord.TextChannel,
    after: discord.Object | None,
) -> None:
    row = await get_channel_progress(guild.id, channel.id)
    if row is None:
        # Created after on_ready: its range starts at the guild boundary.
        await apply_counts(
            guild.id, channel.id, {}, range_start=self._boundary[guild.id]
        )
        row = await get_channel_progress(guild.id, channel.id)
    if row.is_completed:
        return
    counts: defaultdict[int, int] = defaultdict(int)
    oldest_id = row.oldest_id
    processed = 0
    try:
        async for message in channel.history(
            limit=None,
            before=discord.Object(id=row.oldest_id),
            after=after,
            oldest_first=False,
        ):
            if not message.author.bot:
                counts[message.author.id] += 1
            oldest_id = message.id
            processed += 1
            if processed % _CHECKPOINT_EVERY == 0:
                await self._write(
                    guild.id,
                    channel.id,
                    counts,
                    range_start=row.oldest_id,
                    oldest_id=oldest_id,
                )
                counts = defaultdict(int)
                self.logger.info(
                    'Stats import: %s — checkpoint at %d messages',
                    channel.name,
                    processed,
                )
    except discord.Forbidden:
        self.logger.warning(
            'Stats import: no permission for #%s, skipping', channel.name
        )
    await self._write(
        guild.id,
        channel.id,
        counts,
        range_start=row.oldest_id,
        oldest_id=oldest_id,
        is_completed=True,
    )


def _drop_guild_state(self, guild_id: int) -> None:
    for mapping in (self._buffer, self._floor):
        for key in [k for k in mapping if k[0] == guild_id]:
            del mapping[key]
    self._caught_up = {k for k in self._caught_up if k[0] != guild_id}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `$PYTEST tests/cogs/test_stats_tracker.py -v`
Expected: all PASS (Task 2 scenarios included).

- [ ] **Step 5: Commit**

```bash
git add sources/lib/stats_tracker.py tests/cogs/test_stats_tracker.py
git commit -m "feat(stats): add resumable backward import, reset and forget to tracker"
```

---

### Task 4: Wire the cog, remove old code, update docs

**Files:**
- Modify: `sources/lib/cogs/stats.py` (rewrite everything except `leaderboard`)
- Modify: `sources/lib/db/operations/stats.py` (remove `increment_message_counts`, `save_channel_progress`, `get_guilds_with_incomplete_import`)
- Modify: `tests/db/test_db_operations.py` (remove `TestGetGuildsWithIncompleteImport`)
- Rewrite: `tests/cogs/test_stats.py`
- Modify: `CLAUDE.md`, `README.md`

**Interfaces:**
- Consumes: the full `StatsTracker` API from Tasks 2–3; `upsert_import_job`, `get_all_channel_progress`, `get_leaderboard`.
- Produces: `StatsCog.tracker: StatsTracker`; `/stats reset`; `_ResetConfirmView(tracker: StatsTracker)` with a `confirm` button.

- [ ] **Step 1: Write the failing cog tests**

Replace `tests/cogs/test_stats.py` with:

```python
"""Tests for StatsCog commands; counting itself is covered by test_stats_tracker."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord

from sources.lib.cogs.stats import StatsCog, _ResetConfirmView


def _interaction(guild_id: int = 1) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.guild.id = guild_id
    interaction.response = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


async def test_import_rejects_invalid_date() -> None:
    cog = StatsCog(MagicMock())
    interaction = _interaction()

    with patch('sources.lib.cogs.stats.upsert_import_job', new=AsyncMock()) as upsert:
        await cog.import_history.callback(cog, interaction, since='2024-13-01')

    upsert.assert_not_awaited()
    assert 'Invalid date' in interaction.response.send_message.call_args.args[0]


async def test_import_records_job_and_starts_import() -> None:
    cog = StatsCog(MagicMock())
    interaction = _interaction()
    cog.tracker.start_import = MagicMock(return_value=True)

    with patch('sources.lib.cogs.stats.upsert_import_job', new=AsyncMock()) as upsert:
        await cog.import_history.callback(cog, interaction, since='2024-01-02')

    since = upsert.await_args.args[1]
    assert (since.year, since.month, since.day) == (2024, 1, 2)
    cog.tracker.start_import.assert_called_once_with(interaction.guild, since)


async def test_import_does_not_restart_running_import() -> None:
    cog = StatsCog(MagicMock())
    interaction = _interaction()
    cog.tracker.is_importing = MagicMock(return_value=True)
    cog.tracker.start_import = MagicMock()

    with patch('sources.lib.cogs.stats.upsert_import_job', new=AsyncMock()) as upsert:
        await cog.import_history.callback(cog, interaction, since=None)

    upsert.assert_not_awaited()
    cog.tracker.start_import.assert_not_called()


async def test_reset_asks_for_confirmation_without_resetting() -> None:
    cog = StatsCog(MagicMock())
    interaction = _interaction()
    cog.tracker.reset = AsyncMock()

    await cog.reset.callback(cog, interaction)

    cog.tracker.reset.assert_not_awaited()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert isinstance(kwargs['view'], _ResetConfirmView)
    assert kwargs['ephemeral'] is True


async def test_reset_confirm_resets_guild() -> None:
    tracker = MagicMock()
    tracker.reset = AsyncMock()
    view = _ResetConfirmView(tracker)
    interaction = _interaction()

    await view.confirm.callback(interaction)

    tracker.reset.assert_awaited_once_with(interaction.guild)
    interaction.edit_original_response.assert_awaited_once()
```

Remove the `TestGetGuildsWithIncompleteImport` class from `tests/db/test_db_operations.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$PYTEST tests/cogs/test_stats.py`
Expected: collection error `ImportError: cannot import name '_ResetConfirmView'`.

- [ ] **Step 3: Rewrite the cog**

Replace `sources/lib/cogs/stats.py` with the following. The `leaderboard` command is copied unchanged from the current file:

```python
"""Stats cog — per-guild message count statistics and leaderboard."""

from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from sources.lib.db.operations.stats import (
    get_all_channel_progress,
    get_leaderboard,
    upsert_import_job,
)
from sources.lib.stats_tracker import StatsTracker
from sources.lib.utils.logger import Logger

_FLUSH_INTERVAL_SECONDS = 30


class _ResetConfirmView(discord.ui.View):
    """Ephemeral confirmation for /stats reset."""

    def __init__(self, tracker: StatsTracker) -> None:
        """Initialise the view.

        Args:
            tracker: The stats tracker that performs the reset.
        """
        super().__init__(timeout=60)
        self.tracker = tracker

    @discord.ui.button(label='Reset statistics', style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Wipe the guild's statistics and start rebuilding them.

        Args:
            interaction: The button interaction.
            button: The pressed button.
        """
        self.stop()
        # Answer first: the reset can wait for a flush and outlast the 3 s deadline.
        await interaction.response.edit_message(content='Resetting…', view=None)
        await self.tracker.reset(interaction.guild)
        await interaction.edit_original_response(
            content='Statistics wiped. Rebuilding from history in the background; '
            'check `/stats import-status` for progress.'
        )


class StatsCog(commands.Cog):
    """Message statistics commands; counting is done by StatsTracker."""

    stats = app_commands.Group(
        name='stats', description='Message statistics and leaderboard'
    )

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog.

        Args:
            bot: The Discord bot instance.
        """
        self.bot = bot
        self.logger = Logger()
        self.tracker = StatsTracker()

    async def cog_load(self) -> None:
        """Start the periodic flush of live counts."""
        self._flush_buffer.start()

    async def cog_unload(self) -> None:
        """Stop background work and write out what can be written."""
        self._flush_buffer.cancel()
        await self.tracker.close()

    @tasks.loop(seconds=_FLUSH_INTERVAL_SECONDS)
    async def _flush_buffer(self) -> None:
        """Periodically write buffered message counts to the database."""
        await self.tracker.flush()

    @_flush_buffer.before_loop
    async def _before_flush(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener('on_message')
    async def on_message(self, message: discord.Message) -> None:
        """Count a live message.

        Args:
            message: The incoming Discord message.
        """
        self.tracker.record(message)

    @commands.Cog.listener('on_ready')
    async def on_ready(self) -> None:
        """Start a counting epoch: catch up on downtime, resume imports."""
        await self.tracker.on_ready(self.bot.guilds)

    @commands.Cog.listener('on_guild_join')
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Start counting a newly joined guild.

        Args:
            guild: The guild the bot joined.
        """
        self.tracker.on_guild_join(guild)

    @commands.Cog.listener('on_guild_remove')
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Stop all stats work for a guild the bot has left.

        Args:
            guild: The guild the bot left.
        """
        await self.tracker.forget(guild.id)

    # <leaderboard command: copy unchanged from the current file>

    @stats.command(
        name='import', description='Import message history to build statistics'
    )
    @app_commands.describe(
        since='Only import messages from this date forward (YYYY-MM-DD).'
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def import_history(
        self,
        interaction: discord.Interaction,
        since: str | None = None,
    ) -> None:
        """Start or resume the background history import for this guild.

        Args:
            interaction: The Discord interaction.
            since: Optional ISO date (YYYY-MM-DD) limiting how far back the import goes.
        """
        if self.tracker.is_importing(interaction.guild_id):
            await interaction.response.send_message(
                'An import is already running. Check progress with `/stats import-status`.',
                ephemeral=True,
            )
            return

        since_dt: datetime | None = None
        if since:
            try:
                since_dt = datetime.strptime(since, '%Y-%m-%d').replace(tzinfo=UTC)
            except ValueError:
                await interaction.response.send_message(
                    'Invalid date format. Use YYYY-MM-DD, e.g. `2023-01-01`.',
                    ephemeral=True,
                )
                return

        await upsert_import_job(interaction.guild_id, since_dt)
        self.tracker.start_import(interaction.guild, since_dt)
        await interaction.response.send_message(
            'Import started in the background. Use `/stats import-status` to check progress.',
            ephemeral=True,
        )

    @stats.command(
        name='import-status', description='Show message history import progress'
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def import_status(self, interaction: discord.Interaction) -> None:
        """Show the state of the history import and downtime catch-up.

        Args:
            interaction: The Discord interaction.
        """
        guild_id = interaction.guild_id
        progress_rows = await get_all_channel_progress(guild_id)
        readable_channels = sum(
            1
            for ch in interaction.guild.text_channels
            if ch.permissions_for(interaction.guild.me).read_message_history
        )
        completed = sum(1 for r in progress_rows if r.is_completed)
        catching_up = self.tracker.catching_up_count(guild_id)

        embed = discord.Embed(title='Import Status', colour=discord.Colour.blurple())
        embed.add_field(
            name='Running',
            value='Yes' if self.tracker.is_importing(guild_id) else 'No',
            inline=True,
        )
        embed.add_field(
            name='Progress',
            value=f'{completed} / {readable_channels} channels done',
            inline=True,
        )
        if catching_up:
            embed.add_field(
                name='Catching up', value=f'{catching_up} channel(s)', inline=True
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @stats.command(
        name='reset', description='Wipe statistics and rebuild them from history'
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset(self, interaction: discord.Interaction) -> None:
        """Ask for confirmation before wiping this guild's statistics.

        Args:
            interaction: The Discord interaction.
        """
        await interaction.response.send_message(
            'This deletes all message statistics for this server and rebuilds them '
            'from channel history. Continue?',
            view=_ResetConfirmView(self.tracker),
            ephemeral=True,
        )
```

- [ ] **Step 4: Remove the unused operations**

Delete `increment_message_counts`, `save_channel_progress` and `get_guilds_with_incomplete_import` from `sources/lib/db/operations/stats.py`. Then confirm nothing references them:

Run: `grep -rn "increment_message_counts\|save_channel_progress\|get_guilds_with_incomplete_import\|last_message_id" sources tests`
Expected: no output, except `last_message_id` inside `sources/lib/db/alembic/versions/` and `tests/db/test_stats_integration.py`.

- [ ] **Step 5: Run the whole suite**

Run: `$PYTEST tests -m "not integration"`
Expected: all PASS, including `tests/cogs/test_permissions.py` (it discovers `/stats reset` automatically).

Run: `$PYTEST tests -m integration`
Expected: all PASS.

- [ ] **Step 6: Update the docs**

`CLAUDE.md`:
- Project structure: under `lib/`, after `scheduler.py`, add
  `│   ├── stats_tracker.py  # StatsTracker — exact-once message counting (live, catch-up, import, reset)`;
  change the `stats.py` cog comment to `# /stats group; delegates counting to StatsTracker`.
- Models: replace the `StatsImportProgress` line with
  ``- `StatsImportProgress(guild_id+channel_id PK, oldest_id, newest_id, is_completed)` — per-channel range of messages already counted; counts and range always move in one transaction (`apply_counts`)``
  and add
  ``- `StatsImportJob(guild_id PK, since nullable)` — requested history import that has not finished; resumed on every `on_ready` ``.
- Commands table: after `/stats import-status`, add `| /stats reset | stats.py | Wipe statistics and rebuild them from history (admin) |` (with backticks around the command, matching neighbouring rows).

`README.md`: after the `/stats import-status` bullet, add
``- `/stats reset` — wipe statistics and rebuild them from history (admin)``.

- [ ] **Step 7: Commit**

```bash
git add sources/lib/cogs/stats.py sources/lib/db/operations/stats.py \
  tests/cogs/test_stats.py tests/db/test_db_operations.py CLAUDE.md README.md
git commit -m "feat(stats): wire exact-once tracker into /stats and add /stats reset"
```

---

### Task 5: Verify the migration against the real database (gated)

**Files:** none.

This migration **wipes** `message_stats` and `stats_import_progress`. While production still runs the old code, running it against the real database empties the leaderboard before the new code is deployed, and the old code then writes to a column that no longer exists.

- [ ] **Step 1: Ask the user**

Ask explicitly whether to run `alembic upgrade head` against the database in `.claude/settings.local.json` now, or to let the deploy apply it (the container runs `alembic upgrade head` on start). Do not run anything until they answer.

- [ ] **Step 2: If approved, verify there is a single head and apply**

Run: `DISCORD_TOKEN=dummy alembic -c sources/alembic.ini heads`
Expected: `3c9e1f7a2b4d (head)`

Run: `DISCORD_TOKEN=dummy alembic -c sources/alembic.ini upgrade head`
Expected: `Running upgrade bc25943c8c1d -> 3c9e1f7a2b4d, exact-once message stats`, no errors.
