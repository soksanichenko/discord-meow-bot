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
        self.deleted = False
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
        if self.deleted:
            raise discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), '')
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
        if self.failures:
            raise self.failures.pop(0)
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
            # raising=False: not every operation is imported by the tracker.
            monkeypatch.setattr(
                f'sources.lib.stats_tracker.{name}', getattr(self, name), raising=False
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
