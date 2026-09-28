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
