"""Tests for StatsCog message count buffering and flushing."""

from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import IntegrityError, OperationalError

from sources.lib.cogs.stats import StatsCog


def _db_error(cls: type) -> Exception:
    return cls('INSERT ...', {}, Exception('db error'))


async def test_flush_keeps_counts_when_db_is_unavailable() -> None:
    cog = StatsCog(MagicMock())
    cog._buffer[1][10] += 3

    with patch(
        'sources.lib.cogs.stats.increment_message_counts',
        new=AsyncMock(side_effect=_db_error(OperationalError)),
    ):
        # Raising here would stop the tasks.loop for good.
        await cog._flush_buffer.coro(cog)

    assert cog._buffer[1][10] == 3


async def test_flush_retries_kept_counts_on_next_run() -> None:
    cog = StatsCog(MagicMock())
    cog._buffer[1][10] += 3
    increment = AsyncMock(side_effect=[_db_error(OperationalError), None])

    with patch('sources.lib.cogs.stats.increment_message_counts', new=increment):
        await cog._flush_buffer.coro(cog)
        cog._buffer[1][10] += 2
        await cog._flush_buffer.coro(cog)

    increment.assert_awaited_with(1, {10: 5})
    assert not cog._buffer


async def test_flush_drops_counts_rejected_by_db_and_flushes_other_guilds() -> None:
    cog = StatsCog(MagicMock())
    cog._buffer[1][10] += 3
    cog._buffer[2][20] += 4

    async def increment(guild_id: int, counts: dict[int, int]) -> None:
        if guild_id == 1:
            # e.g. the bot left guild 1 and its row is gone.
            raise _db_error(IntegrityError)

    increment_mock = AsyncMock(side_effect=increment)
    with patch('sources.lib.cogs.stats.increment_message_counts', new=increment_mock):
        await cog._flush_buffer.coro(cog)

    increment_mock.assert_any_await(2, {20: 4})
    assert not cog._buffer
