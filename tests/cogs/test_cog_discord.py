"""Discord Cog tests — loading verification and command handler logic.

Cog-load tests: verify every cog instantiates cleanly and exposes the
expected top-level slash commands/groups. No cog_load() is called, so no
network or DB side effects occur.

Command handler tests: exercise command methods directly using a mocked
discord.Interaction. Discord objects (Embed, View) are real instances —
only IO (DB calls, Discord API) is mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from sources.lib.cogs.admin import AdminCog
from sources.lib.cogs.birthdays import BirthdaysCog
from sources.lib.cogs.domain_fixer import DomainFixerCog
from sources.lib.cogs.events import EventsCog
from sources.lib.cogs.guild import GuildCog
from sources.lib.cogs.help import HelpCog
from sources.lib.cogs.messages import MessagesCog
from sources.lib.cogs.music_links import MusicLinksCog
from sources.lib.cogs.reminders import RemindersCog
from sources.lib.cogs.stats import StatsCog
from sources.lib.cogs.telegram_relay import TelegramRelayCog
from sources.lib.cogs.twitch_relay import TwitchRelayCog
from sources.lib.cogs.user import UserCog
from sources.lib.cogs.voice import VoiceCog
from sources.lib.cogs.youtube_relay import YouTubeRelayCog
from sources.lib.utils.get_timestamp import TimestampFormatView


def _bot() -> MagicMock:
    bot = MagicMock(spec=commands.Bot)
    bot.intents = discord.Intents.default()
    return bot


def _interaction(*, user_id: int = 1, display_name: str = 'User') -> AsyncMock:
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.user.display_name = display_name
    interaction.user.name = display_name.lower()
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.locale = MagicMock()
    interaction.locale.value = 'en-US'
    return interaction


def _cmd_names(cog: commands.Cog) -> set[str]:
    return {cmd.name for cmd in cog.__cog_app_commands__}


# ---------------------------------------------------------------------------
# Cog-load tests
# ---------------------------------------------------------------------------


class TestCogLoading:
    """Every cog instantiates without errors and registers the expected commands."""

    def test_user_cog(self) -> None:
        names = _cmd_names(UserCog(_bot()))
        assert names == {
            'set-timezone',
            'force-timezone',
            'my-settings',
            'get-timestamp',
            'timezones',
        }

    def test_guild_cog(self) -> None:
        assert 'server' in _cmd_names(GuildCog(_bot()))

    def test_birthdays_cog(self) -> None:
        assert 'birthday' in _cmd_names(BirthdaysCog(_bot()))

    def test_reminders_cog(self) -> None:
        assert 'reminders' in _cmd_names(RemindersCog(_bot()))

    def test_stats_cog(self) -> None:
        assert 'stats' in _cmd_names(StatsCog(_bot()))

    def test_domain_fixer_cog(self) -> None:
        assert 'domain-fixer' in _cmd_names(DomainFixerCog(_bot()))

    def test_music_links_cog(self) -> None:
        assert 'music-links' in _cmd_names(MusicLinksCog(_bot()))

    def test_telegram_relay_cog(self) -> None:
        assert 'telegram-relay' in _cmd_names(TelegramRelayCog(_bot()))

    def test_youtube_relay_cog(self) -> None:
        assert 'youtube-relay' in _cmd_names(YouTubeRelayCog(_bot()))

    def test_twitch_relay_cog(self) -> None:
        assert 'twitch-relay' in _cmd_names(TwitchRelayCog(_bot()))

    def test_help_cog(self) -> None:
        assert 'help' in _cmd_names(HelpCog(_bot()))

    def test_events_cog_has_no_slash_commands(self) -> None:
        assert _cmd_names(EventsCog(_bot())) == set()

    def test_messages_cog_has_no_slash_commands(self) -> None:
        assert _cmd_names(MessagesCog(_bot())) == set()

    def test_admin_cog_slash_commands(self) -> None:
        assert _cmd_names(AdminCog(_bot())) == {'bot-stats'}

    def test_voice_cog_has_no_slash_commands(self) -> None:
        assert _cmd_names(VoiceCog(_bot())) == set()


# ---------------------------------------------------------------------------
# UserCog command handler tests
# ---------------------------------------------------------------------------


class TestMySettings:
    """/my-settings responds with an embed containing the user's timezone."""

    async def test_with_timezone_set(self) -> None:
        cog = UserCog(_bot())
        interaction = _interaction()
        db_user = SimpleNamespace(timezone='Europe/Kyiv')
        with patch(
            'sources.lib.cogs.user.get_user', new=AsyncMock(return_value=db_user)
        ):
            await cog.my_settings.callback(cog, interaction)

        embed = interaction.response.send_message.call_args.kwargs['embed']
        assert embed.fields[0].value == 'Europe/Kyiv'

    async def test_without_timezone_shows_not_set(self) -> None:
        cog = UserCog(_bot())
        interaction = _interaction()
        with patch('sources.lib.cogs.user.get_user', new=AsyncMock(return_value=None)):
            await cog.my_settings.callback(cog, interaction)

        embed = interaction.response.send_message.call_args.kwargs['embed']
        assert embed.fields[0].value == '*not set*'


class TestSetTimezone:
    """/set-timezone upserts the user record and confirms the change."""

    async def test_upserts_user_and_replies(self) -> None:
        cog = UserCog(_bot())
        interaction = _interaction(display_name='Alice')
        with patch('sources.lib.cogs.user.upsert_user', new=AsyncMock()) as mock_upsert:
            await cog.set_timezone.callback(
                cog, interaction, timezone='America/New_York'
            )

        mock_upsert.assert_awaited_once()
        msg = interaction.response.send_message.call_args.args[0]
        assert 'America/New_York' in msg
        assert 'Alice' in msg


class TestGetTimestamp:
    """/get-timestamp dispatches based on timezone and date validity."""

    async def test_no_timezone_exits_early(self) -> None:
        cog = UserCog(_bot())
        interaction = _interaction()
        with patch(
            'sources.lib.cogs.user.require_timezone', new=AsyncMock(return_value=None)
        ):
            await cog.get_timestamp.callback(cog, interaction)

        interaction.response.send_message.assert_not_awaited()

    async def test_invalid_date_sends_error(self) -> None:
        cog = UserCog(_bot())
        interaction = _interaction()
        db_user = SimpleNamespace(timezone='UTC')
        with patch(
            'sources.lib.cogs.user.require_timezone',
            new=AsyncMock(return_value=db_user),
        ):
            with patch('sources.lib.cogs.user.parse_and_validate', return_value=None):
                await cog.get_timestamp.callback(
                    cog, interaction, time='notatime', date=''
                )

        msg = interaction.response.send_message.call_args.args[0]
        assert 'incorrect format' in msg

    async def test_valid_date_sends_format_view(self) -> None:
        cog = UserCog(_bot())
        interaction = _interaction()
        db_user = SimpleNamespace(timezone='UTC')
        dt = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        with patch(
            'sources.lib.cogs.user.require_timezone',
            new=AsyncMock(return_value=db_user),
        ):
            with patch('sources.lib.cogs.user.parse_and_validate', return_value=dt):
                await cog.get_timestamp.callback(
                    cog, interaction, time='12:00', date='2026-06-01'
                )

        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert isinstance(call_kwargs['view'], TimestampFormatView)
        assert call_kwargs['view'].timestamp == int(dt.timestamp())
