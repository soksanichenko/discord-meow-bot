"""Permission enforcement tests for admin slash commands.

Discord only honours ``default_member_permissions`` on top-level commands, and
guild admins can override it in the Integrations settings, so it is a UI hint
rather than a security boundary. Every admin command must therefore also carry
a runtime permission check.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from sources.lib.cogs.admin import AdminCog
from sources.lib.cogs.auto_responder import AutoResponderCog
from sources.lib.cogs.birthdays import BirthdaysCog
from sources.lib.cogs.domain_fixer import DomainFixerCog
from sources.lib.cogs.guild import GuildCog
from sources.lib.cogs.help import HelpCog
from sources.lib.cogs.music_links import MusicLinksCog
from sources.lib.cogs.reminders import RemindersCog
from sources.lib.cogs.stats import StatsCog
from sources.lib.cogs.telegram_relay import TelegramRelayCog
from sources.lib.cogs.twitch_relay import TwitchRelayCog
from sources.lib.cogs.user import UserCog
from sources.lib.cogs.youtube_relay import YouTubeRelayCog

_COGS = (
    AdminCog,
    AutoResponderCog,
    BirthdaysCog,
    DomainFixerCog,
    GuildCog,
    HelpCog,
    MusicLinksCog,
    RemindersCog,
    StatsCog,
    TelegramRelayCog,
    TwitchRelayCog,
    UserCog,
    YouTubeRelayCog,
)


def _bot() -> MagicMock:
    bot = MagicMock(spec=commands.Bot)
    bot.intents = discord.Intents.default()
    return bot


def _required_permissions(cmd: app_commands.Command) -> discord.Permissions | None:
    """Return the permissions an admin command is meant to require, if any."""
    if cmd.default_permissions is not None:
        return cmd.default_permissions
    return cmd.root_parent.default_permissions if cmd.root_parent else None


def _admin_commands() -> list[app_commands.Command]:
    result = []
    for cog_cls in _COGS:
        for cmd in cog_cls(_bot()).walk_app_commands():
            if isinstance(cmd, app_commands.Command) and _required_permissions(cmd):
                result.append(cmd)
    return result


_ADMIN_COMMANDS = _admin_commands()


def test_admin_commands_discovered() -> None:
    names = {cmd.qualified_name for cmd in _ADMIN_COMMANDS}
    assert {'birthday role-set', 'server timezone-set', 'twitch-relay add'} <= names


@pytest.mark.parametrize(
    'cmd', _ADMIN_COMMANDS, ids=[c.qualified_name for c in _ADMIN_COMMANDS]
)
async def test_admin_command_rejects_member_without_permissions(
    cmd: app_commands.Command,
) -> None:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.permissions = discord.Permissions.none()

    with pytest.raises(app_commands.MissingPermissions):
        for predicate in cmd.checks:
            await discord.utils.maybe_coroutine(predicate, interaction)


@pytest.mark.parametrize(
    'cog_cls, attr',
    [
        (MusicLinksCog, 'music_links'),
        (TelegramRelayCog, 'relay'),
        (YouTubeRelayCog, 'relay'),
        (TwitchRelayCog, 'relay'),
    ],
)
def test_admin_only_group_is_hidden_from_members(cog_cls: type, attr: str) -> None:
    group = getattr(cog_cls(_bot()), attr)
    payload = group.to_dict(MagicMock())
    assert (
        payload['default_member_permissions']
        == discord.Permissions(manage_guild=True).value
    )


async def test_birthday_role_set_rejects_role_above_invoker() -> None:
    cog = BirthdaysCog(_bot())
    role = MagicMock(spec=discord.Role)
    role.name = 'Moderator'
    role.managed = False
    role.is_default.return_value = False
    role.__ge__ = lambda self, other: other is interaction.user.top_role
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.top_role = MagicMock(spec=discord.Role)
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.guild.owner_id = 999
    interaction.guild.me.top_role = MagicMock(spec=discord.Role)

    with patch(
        'sources.lib.cogs.birthdays.upsert_guild_settings', new=AsyncMock()
    ) as upsert:
        await cog.role_set.callback(cog, interaction, role)

    upsert.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
