"""Tests for EventsCog scheduling logic and _start_event."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord

from sources.lib.cogs.events import EventsCog, _start_event


def _make_event(
    *,
    event_id: int = 1,
    guild_id: int = 100,
    name: str = 'Test',
    start_time: datetime | None = None,
    status: discord.EventStatus = discord.EventStatus.scheduled,
) -> MagicMock:
    event = MagicMock(spec=discord.ScheduledEvent)
    event.id = event_id
    event.guild_id = guild_id
    event.name = name
    event.start_time = start_time or datetime.now(UTC) + timedelta(hours=1)
    event.status = status
    return event


def _make_cog() -> EventsCog:
    return EventsCog(MagicMock())


class TestSchedule:
    """EventsCog._schedule registers APScheduler date jobs correctly."""

    def test_future_event_uses_start_time(self) -> None:
        cog = _make_cog()
        run_at = datetime.now(UTC) + timedelta(hours=2)
        cog._schedule(_make_event(event_id=1, guild_id=100, start_time=run_at))
        job = cog._scheduler.get_job('event_100_1')
        assert job is not None
        assert abs((job.trigger.run_date - run_at).total_seconds()) < 1

    def test_overdue_event_fires_within_seconds(self) -> None:
        cog = _make_cog()
        past = datetime.now(UTC) - timedelta(minutes=10)
        cog._schedule(_make_event(event_id=2, guild_id=100, start_time=past))
        job = cog._scheduler.get_job('event_100_2')
        assert job is not None
        delta = (job.trigger.run_date - datetime.now(UTC)).total_seconds()
        assert 0 < delta <= 10

    async def test_reschedule_replaces_existing_job(self) -> None:
        # replace_existing=True only takes effect on a started scheduler.
        cog = _make_cog()
        cog._scheduler.start()
        try:
            event = _make_event(
                event_id=3,
                guild_id=100,
                start_time=datetime.now(UTC) + timedelta(hours=1),
            )
            cog._schedule(event)

            new_time = datetime.now(UTC) + timedelta(hours=3)
            event.start_time = new_time
            cog._schedule(event)

            job = cog._scheduler.get_job('event_100_3')
            assert abs((job.trigger.run_date - new_time).total_seconds()) < 1
        finally:
            cog._scheduler.shutdown(wait=False)

    def test_naive_datetime_gets_utc_tzinfo(self) -> None:
        cog = _make_cog()
        naive = datetime(2030, 6, 1, 12, 0, 0)
        cog._schedule(_make_event(event_id=4, guild_id=100, start_time=naive))
        job = cog._scheduler.get_job('event_100_4')
        assert job is not None
        assert job.trigger.run_date.tzinfo is not None


class TestCancel:
    """EventsCog._cancel removes jobs and is a no-op when absent."""

    def test_removes_existing_job(self) -> None:
        cog = _make_cog()
        cog._schedule(_make_event(event_id=10, guild_id=100))
        assert cog._scheduler.get_job('event_100_10') is not None
        cog._cancel(100, 10)
        assert cog._scheduler.get_job('event_100_10') is None

    def test_no_error_when_job_absent(self) -> None:
        cog = _make_cog()
        cog._cancel(100, 999)  # must not raise


class TestStartEvent:
    """_start_event correctly gates on guild/event availability and status."""

    async def test_guild_not_found_returns_silently(self) -> None:
        bot = MagicMock()
        bot.get_guild.return_value = None
        await _start_event(1, 1, bot)

    async def test_event_not_found_returns_silently(self) -> None:
        response = MagicMock()
        response.status = 404
        response.reason = 'Not Found'
        guild = AsyncMock()
        guild.fetch_scheduled_event.side_effect = discord.NotFound(response, 'Unknown')
        bot = MagicMock()
        bot.get_guild.return_value = guild
        await _start_event(1, 1, bot)
        guild.fetch_scheduled_event.assert_awaited_once_with(1)

    async def test_already_active_event_skips_edit(self) -> None:
        event = AsyncMock()
        event.status = discord.EventStatus.active
        guild = AsyncMock()
        guild.fetch_scheduled_event.return_value = event
        bot = MagicMock()
        bot.get_guild.return_value = guild
        await _start_event(1, 1, bot)
        event.edit.assert_not_awaited()

    async def test_scheduled_event_is_started(self) -> None:
        event = AsyncMock()
        event.status = discord.EventStatus.scheduled
        event.name = 'Test Event'
        guild = AsyncMock()
        guild.name = 'Test Guild'
        guild.fetch_scheduled_event.return_value = event
        bot = MagicMock()
        bot.get_guild.return_value = guild
        await _start_event(1, 42, bot)
        event.edit.assert_awaited_once_with(status=discord.EventStatus.active)
