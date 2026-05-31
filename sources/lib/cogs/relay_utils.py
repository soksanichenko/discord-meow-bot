"""Shared utilities for relay cogs (Twitch, YouTube)."""

from collections import Counter
from collections.abc import Callable

import discord
from discord import app_commands
from discord.ext import commands


async def resolve_channel(
    bot: commands.Bot,
    channel_id: int,
) -> discord.abc.GuildChannel | discord.Thread | None:
    """Get a Discord channel by ID, falling back to the API if not in cache.

    Args:
        bot: The Discord bot instance.
        channel_id: Discord channel ID to resolve.

    Returns:
        The channel, or None if not found.
    """
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.NotFound:
            return None
    return channel


async def parse_relay_id(
    value: str,
    interaction: discord.Interaction,
) -> int | None:
    """Parse a relay ID from an autocomplete string value.

    Sends an ephemeral error and returns None if the value is not a valid integer
    (i.e. the user typed instead of selecting from the autocomplete list).

    Args:
        value: String value from the autocomplete parameter.
        interaction: The Discord interaction used to send the error response.

    Returns:
        Parsed integer relay ID, or None on failure.
    """
    try:
        return int(value)
    except ValueError:
        await interaction.response.send_message(
            'Please select a channel from the list.',
            ephemeral=True,
        )
        return None


def build_relay_choices(
    relays: list,
    current: str,
    guild: discord.Guild,
    get_name: Callable[[object], str],
    get_key: Callable[[object], object],
) -> list[app_commands.Choice[str]]:
    """Build autocomplete choices for a relay list with channel disambiguation.

    When the same platform channel is forwarded to multiple Discord channels,
    the Discord channel name is appended in parentheses to disambiguate entries.

    Args:
        relays: List of relay ORM objects with a discord_channel_id attribute.
        current: Current autocomplete input used for filtering.
        guild: The Discord guild used to resolve channel names.
        get_name: Returns the display name for a relay row.
        get_key: Returns the key used to detect duplicate platform channels.

    Returns:
        Up to 25 filtered and disambiguated Choice objects (value = relay ID).
    """
    key_counts = Counter(get_key(r) for r in relays)
    choices = []
    for r in relays:
        name = get_name(r)
        if key_counts[get_key(r)] > 1:
            ch = guild.get_channel(r.discord_channel_id)
            ch_name = f'#{ch.name}' if ch else f'#{r.discord_channel_id}'
            name = f'{name} ({ch_name})'
        if current.lower() in name.lower():
            choices.append(app_commands.Choice(name=name, value=str(r.id)))
    return choices[:25]
