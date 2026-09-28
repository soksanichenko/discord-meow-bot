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
    delete_import_job,
    get_all_channel_progress,
    get_channel_progress,
    get_import_jobs,
    reset_guild_stats,
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
            by_id = {guild.id: guild for guild in guilds}
            for job in await get_import_jobs():
                guild = by_id.get(job.guild_id)
                if guild is not None:
                    self._start_import(guild, job.since)

    def on_guild_join(self, guild: discord.Guild) -> None:
        """Start counting a newly joined guild live.

        Args:
            guild: The guild the bot joined.
        """
        self._boundary[guild.id] = now_snowflake()

    async def request_import(
        self, guild: discord.Guild, since: datetime | None
    ) -> bool:
        """Start the backward history import for a guild unless one is running.

        Args:
            guild: The guild to import.
            since: Lower time bound; None imports the whole history.

        Returns:
            True if a new import was started.
        """
        # Under the lock, so an import can never start inside a reset's window.
        async with self._lock:
            return self._start_import(guild, since)

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
            # A half-done reset would leave stale ranges with no import to fix them.
            await self._retrying(reset_guild_stats, guild.id)
            self._start_import(guild, since=None)

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

    def _start_import(self, guild: discord.Guild, since: datetime | None) -> bool:
        # A guild without a boundary was forgotten (or never became ready).
        if guild.id not in self._boundary or self.is_importing(guild.id):
            return False
        self._import_tasks[guild.id] = asyncio.create_task(
            self._import_guild(guild, since)
        )
        return True

    async def _import_guild(self, guild: discord.Guild, since: datetime | None) -> None:
        after = (
            discord.Object(id=discord.utils.time_snowflake(since)) if since else None
        )
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
        except (discord.Forbidden, discord.NotFound):
            self.logger.warning(
                'Stats import: #%s is gone or not readable, skipping', channel.name
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
