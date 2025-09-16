"""Main module of the bot"""

from __future__ import annotations

import asyncio
from typing import Optional

import discord
from discord import utils, Color
from discord.ext.commands import Bot

from sources.config import config
from sources.lib.commands.get_timestamp import (
    parse_and_validate,
    autocomplete_timezone,
    role_autocomplete,
)
from sources.lib.commands.get_timestamp import TimestampFormatView
from sources.lib.commands.utils import get_command
from sources.lib.core import BotAvatar
from sources.lib.db.models import Guild, User
from sources.lib.db import AsyncSession
from sources.lib.db.crud.base import (
    update_db_entity_or_create,
    get_db_entity,
)
from sources.lib.on_message.domains_fixer import fix_urls
from sources.lib.utils import Logger

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.members = True
bot = Bot(
    command_prefix='?',
    intents=intents,
    activity=discord.Game('Rolling the balls of wool'),
    status=discord.Status.invisible,
    avatar=BotAvatar(),
)


@bot.command(
    name='sync-tree',
    description='Sync a tree of the commands',
)
async def sync_tree(context: discord.ext.commands.Context):
    """Sync a tree of the commands"""
    if await bot.is_owner(context.author):
        await bot.tree.sync()
        message = 'Syncing is completed'
    else:
        message = 'You are not an owner of the bot'
    Logger().info(message)
    await context.reply(message)


@bot.tree.command(
    name='info',
    description='Get info about your server',
)
async def info(interaction: discord.Interaction):
    """
    Get info about your server
    :param interaction: the command's interaction
    :return: None
    """
    guild = interaction.guild
    embed_var = discord.Embed(
        title="Server info",
        description=f"Total information about {guild.name}",
        color=Color.brand_green(),
    )
    if guild.banner:
        embed_var.set_image(url=guild.banner.url)
    embed_var.add_field(
        name='Owner',
        value=guild.owner.mention,
        inline=False,
    )
    embed_var.add_field(
        name="Creation date",
        value=guild.created_at.strftime('%Y-%M-%d %H:%M:%S'),
        inline=False,
    )
    embed_var.add_field(
        name="Members count",
        value=guild.member_count,
        inline=False,
    )
    embed_var.add_field(
        name='Nitro boost',
        value=f'Tier {guild.premium_tier}: '
        f'{guild.premium_subscription_count} boost out of 14',
        inline=False,
    )
    embed_var.add_field(
        name='Bitrate limit',
        value=f'{int(guild.bitrate_limit // 1000)} Kbps',
        inline=False,
    )
    embed_var.add_field(
        name='Emoji limit',
        value=guild.emoji_limit,
        inline=False,
    )
    embed_var.add_field(
        name='Sticker limit',
        value=guild.sticker_limit,
        inline=False,
    )
    embed_var.add_field(
        name='File size limit',
        value=f'{guild.filesize_limit // 1024 // 1024} MB',
        inline=False,
    )
    await interaction.response.send_message(embed=embed_var, ephemeral=True)


@bot.tree.command(
    name='set-timezone',
    description='Set a current timezone of user',
)
@discord.app_commands.autocomplete(timezone=autocomplete_timezone)
async def set_timezone(
    interaction: discord.Interaction,
    timezone: str,
):
    """
    Set a current timezone of user
    :param interaction: the command's interaction
    :param timezone: a current timezone of user
    :return: None
    """
    user = interaction.user
    async with AsyncSession() as db_session:
        await update_db_entity_or_create(
            db_session=db_session,
            table_class=User,
            filters={
                'id': user.id,
            },
            updates={
                'timezone': timezone,
                'name': user.name,
            },
        )
    await interaction.response.send_message(
        f'Timezone for user **{user.display_name}** is set to **{timezone}**',
        ephemeral=True,
    )


@bot.tree.context_menu(name='Remove fixed message')
async def remove_fixed_message(
    interaction: discord.Interaction,
    message: discord.Message,
):
    """Remove fixed message using a bot's command"""
    if message.author == bot.user and message.content.endswith(
        f"\nOriginal message posted by {interaction.user.mention}",
    ):
        await message.delete()
        await interaction.response.send_message(
            'The message is deleted',
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            'That message is not yours',
            ephemeral=True,
        )


@bot.tree.command(
    name='list-members',
    description='List of a role members',
)
@discord.app_commands.autocomplete(role=role_autocomplete)
async def list_members(
    interaction: discord.Interaction,
    role: str,
):
    """Print a role members"""
    role_obj = interaction.guild.get_role(int(role))
    if not role_obj:
        await interaction.response.send_message(
            'Role does not found',
            ephemeral=True,
        )
        return

    members = sorted([member.mention for member in role_obj.members])
    embed = discord.Embed(
        title=f'Members of role "{role_obj.name}"',
        color=discord.Color.blue(),
    )
    if not members:
        embed.description = 'That role has no members'
    else:
        embed.description = '\n'.join(members)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@discord.app_commands.describe(
    time='Please input a time in any suitable format in your region'
)
@discord.app_commands.describe(
    date='Please input a date in any suitable format in your region'
)
@bot.tree.command(
    name='get-timestamp',
    description='Get formatted timestamp for any date and/or time',
)
async def get_timestamp(
    interaction: discord.Interaction,
    time: str = '',
    date: str = '',
):
    """
    Send any text by the bot
    :param time: an input time for converting
    :param date: an input date for converting
    :param interaction: the command's interaction
    :return: None
    """
    async with AsyncSession() as db_session:
        user = await get_db_entity(
            db_session=db_session,
            table_class=User,
            id=interaction.user.id,
        )  # type: User
    command_name = 'set-timezone'
    command = await get_command(
        commands_tree=bot.tree,
        command_name=command_name,
    )
    if user is None:
        await interaction.response.send_message(
            f'User **{interaction.user.display_name}** '
            'does not have a timezone.\n'
            f'Please, use command </{command_name}:{command.id}> to set it',
            ephemeral=True,
        )
        return
    time_date = parse_and_validate(
        timezone=user.timezone,
        date=date,
        time=time,
        interaction=interaction,
    )
    if time_date is None:
        await interaction.response.send_message(
            'You sent a date/time in incorrect format',
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        'Select format',
        view=TimestampFormatView(int(time_date.timestamp())),
        ephemeral=True,
    )


def _get_member_activity(members: list[discord.Member]) -> Optional[discord.Activity]:
    """Get a member activity"""
    for member in members:
        game = next((a for a in member.activities if a.type == discord.ActivityType.playing), None)
        if game:
            return game


async def update_channel_status(voice_channel):
    """Update a channel status"""
    members = voice_channel.members
    if members:
        game = _get_member_activity(members)
        status = f"[auto] 🎮 {game.name}" if game else None
        try:
            await voice_channel.edit(status=status)
        except discord.errors.Forbidden:
            pass


@bot.listen('on_presence_update')
async def on_presence_update(before, after):
    """Listen on presence update"""
    if after.voice and after.voice.channel:
        await update_channel_status(after.voice.channel)


@bot.listen('on_voice_state_update')
async def on_voice_state_update(member, before, after):
    """Listen voice channel state update"""
    channel = (after.channel or before.channel)
    await update_channel_status(channel)


@bot.listen('on_message')
async def process_links_in_message(message: discord.Message):
    """
    Process links in a new message
    :param message: a new message posted in Discord
    :return: None
    """

    Logger().info('Get message from %s', message.author.name)
    if message.author == bot.user:
        Logger().info('That message is mine')
        return
    content = fix_urls(message=message)
    if content == message.content:
        Logger().info('The original message is already fine')
        return
    if message.reference is None:
        await message.channel.send(content=content, silent=True)
    else:
        await message.reference.resolved.reply(content=content, silent=True)
    await message.delete()


async def main():
    """Main run function"""
    utils.setup_logging()
    await bot.start(
        token=config.discord_token,
        reconnect=True,
    )


@bot.listen('on_guild_join')
@bot.listen('on_guild_update')
async def add_guild_to_db(guild: discord.Guild):
    """
    Add a guild to DB if a new guild is joined or an existing guild is updated
    """
    async with AsyncSession() as db_session:
        await update_db_entity_or_create(
            db_session=db_session,
            table_class=Guild,
            filters={
                'id': guild.id,
            },
            updates={
                'name': guild.name,
            },
        )


if __name__ == '__main__':
    asyncio.run(main())
