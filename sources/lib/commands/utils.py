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
    commands = await commands_tree.fetch_commands()
    return next(iter(filter(lambda c: c.name == command_name, commands)))
