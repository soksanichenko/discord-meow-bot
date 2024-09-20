"""
Utils for the bot commands
"""

import discord


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
) -> discord.Status:
    """Get the status of a user"""
    return interaction.guild.get_member(user_id).status


def get_user_activity(
    user_id: int,
    interaction: discord.Interaction,
) -> discord.Activity:
    """Get the activity of a user"""
    return interaction.guild.get_member(user_id).activity


def check_is_guild_owner(interaction: discord.Interaction) -> bool:
    """Check an interaction's user is a guild owner"""
    return interaction.guild.owner == interaction.user
