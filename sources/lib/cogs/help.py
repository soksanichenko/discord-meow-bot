"""Help cog — dynamic /help command that reflects the live command tree."""

import discord
from discord import app_commands
from discord.ext import commands

from sources.lib.utils import Logger


def _visible_commands(
    tree: app_commands.CommandTree, guild: discord.Guild | None
) -> dict[str, app_commands.Command | app_commands.Group]:
    """Return all non-context-menu commands available in this guild."""
    merged = {
        cmd.name: cmd
        for cmd in (*tree.get_commands(), *tree.get_commands(guild=guild))
        if not isinstance(cmd, app_commands.ContextMenu)
    }
    return merged


async def _command_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    cmds = _visible_commands(interaction.client.tree, interaction.guild)
    return [
        app_commands.Choice(name=name, value=name)
        for name in sorted(cmds)
        if current.lower() in name.lower()
    ][:25]


def _format_params(cmd: app_commands.Command) -> str:
    """Return a one-line parameter hint, e.g. `<name>` `[note]`."""
    return ' '.join(
        f'`<{p.name}>`' if p.required else f'`[{p.name}]`' for p in cmd.parameters
    )


class HelpCog(commands.Cog):
    """Dynamic /help command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.logger = Logger()

    @app_commands.command(name='help', description='Show help for a bot command')
    @app_commands.describe(command='Command name — leave empty to list all commands')
    @app_commands.autocomplete(command=_command_autocomplete)
    async def help_command(
        self, interaction: discord.Interaction, command: str | None = None
    ) -> None:
        """Show available commands or detailed help for one command."""
        cmds = _visible_commands(self.bot.tree, interaction.guild)

        if command is None:
            embed = discord.Embed(
                title='Available Commands',
                description='Use `/help <command>` for details on a specific command.',
                color=discord.Color.blurple(),
            )
            for name, cmd in sorted(cmds.items()):
                embed.add_field(
                    name=f'/{name}', value=cmd.description or '—', inline=False
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        cmd = cmds.get(command)
        if cmd is None:
            await interaction.response.send_message(
                f'Unknown command: `/{command}`', ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f'/{cmd.name}',
            description=cmd.description or '—',
            color=discord.Color.blurple(),
        )

        if isinstance(cmd, app_commands.Group):
            for sub in sorted(cmd.commands, key=lambda c: c.name):
                hint = (
                    _format_params(sub)
                    if isinstance(sub, app_commands.Command) and sub.parameters
                    else ''
                )
                value = sub.description or '—'
                if hint:
                    value = f'{value}\n{hint}'
                embed.add_field(
                    name=f'/{cmd.name} {sub.name}', value=value, inline=False
                )
        elif isinstance(cmd, app_commands.Command) and cmd.parameters:
            for param in cmd.parameters:
                label = f'`<{param.name}>`' if param.required else f'`[{param.name}]`'
                embed.add_field(
                    name=label, value=param.description or '—', inline=False
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)
