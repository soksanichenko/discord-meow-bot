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
