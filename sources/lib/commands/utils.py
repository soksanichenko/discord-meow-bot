"""
Utils for the bot commands
"""
import discord
from discord.ext import commands

from sources.lib.db.models import User
from sources.lib.db.operations.users import get_user


async def require_timezone(
    bot: commands.Bot,
    interaction: discord.Interaction,
) -> User | None:
    """Fetch the user and send an error response if no timezone is configured.

    Intended to be called at the start of any command that requires the user to
    have run /set-timezone first. Sends an ephemeral message with a clickable
    command mention and returns None so the caller can immediately return.

    Args:
        bot: The Discord bot instance, used to resolve the /set-timezone mention.
        interaction: The interaction to respond to on failure.

    Returns:
        The User instance if a timezone is configured, otherwise None.
    """
    db_user = await get_user(interaction.user.id)
    if db_user is None:
        command = await get_command(bot.tree, 'set-timezone')
        await interaction.response.send_message(
            f'User **{interaction.user.display_name}** does not have a timezone.\n'
            f'Please, use command </set-timezone:{command.id}> to set it',
            ephemeral=True,
        )
        return None
    return db_user


async def get_command(
    commands_tree: discord.app_commands.CommandTree,
    command_name: str,
) -> discord.app_commands.Command:
    """
    Get a command of the bot
    :param commands_tree: A tree of commands
    :param command_name: A name of command
    :return: object of Command
    """
    cmds = await commands_tree.fetch_commands()
    return next(iter(filter(lambda c: c.name == command_name, cmds)))


def get_user_status(
    user_id: int,
    interaction: discord.Interaction,
) -> discord.Status | None:
    """Get the status of a user"""
    member = interaction.guild.get_member(user_id)
    return member.status if member is not None else None


def get_user_activity(
    user_id: int,
    interaction: discord.Interaction,
) -> discord.Activity | None:
    """Get the activity of a user"""
    member = interaction.guild.get_member(user_id)
    return member.activity if member is not None else None


def check_is_guild_owner(interaction: discord.Interaction) -> bool:
    """Check an interaction's user is a guild owner"""
    return interaction.guild.owner == interaction.user
