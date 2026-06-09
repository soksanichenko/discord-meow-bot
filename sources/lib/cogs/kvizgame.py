"""KvizGame cog — commands for uploading packs and managing game sessions."""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from sources.config import config
from sources.lib.kvizgame.game import GameMachine, Settings
from sources.lib.kvizgame.parser import load
from sources.lib.kvizgame.session import GameSession
from sources.lib.utils.logger import Logger

if TYPE_CHECKING:
    pass

_MAX_PACK_SIZE = 100 * 1024 * 1024  # 100 MB


class KvizGameCog(commands.Cog):
    """Commands for managing KvizGame packs and sessions.

    Args:
        bot: The Discord bot instance.
        sessions: Shared session registry — same dict used by the WebSocket server.
    """

    def __init__(self, bot: commands.Bot, sessions: dict[str, GameSession]) -> None:
        self.bot = bot
        self._sessions = sessions
        self._packs_dir = pathlib.Path(config.kvizgame_packs_dir)
        self._packs_dir.mkdir(parents=True, exist_ok=True)
        self.logger = Logger()

    kvizgame = app_commands.Group(name='kvizgame', description='KvizGame quiz game')

    # ------------------------------------------------------------------
    # /kvizgame upload
    # ------------------------------------------------------------------

    @kvizgame.command(
        name='upload', description='Upload a .siq pack file to the server'
    )
    @app_commands.describe(pack='The .siq pack file to upload')
    @app_commands.default_permissions(manage_guild=True)
    async def upload(
        self, interaction: discord.Interaction, pack: discord.Attachment
    ) -> None:
        """Download and save a .siq pack file uploaded by the user.

        Args:
            interaction: The Discord interaction.
            pack: The uploaded .siq attachment.
        """
        await interaction.response.defer(ephemeral=True)

        if not pack.filename.lower().endswith('.siq'):
            await interaction.followup.send(
                'Only .siq files are supported.', ephemeral=True
            )
            return

        if pack.size > _MAX_PACK_SIZE:
            await interaction.followup.send(
                f'File must be 100 MB or smaller'
                f' (received {pack.size / 1024 / 1024:.1f} MB).',
                ephemeral=True,
            )
            return

        safe_name = pathlib.Path(pack.filename).name
        dest = self._packs_dir / safe_name

        data = await pack.read()
        dest.write_bytes(data)

        try:
            pkg = load(str(dest))
        except Exception as exc:
            dest.unlink(missing_ok=True)
            await interaction.followup.send(f'Invalid .siq file: {exc}', ephemeral=True)
            return

        rounds = len(pkg.package.rounds)
        self.logger.info('Pack %r uploaded (%d round(s))', safe_name, rounds)
        await interaction.followup.send(
            f'Pack **{pkg.package.name}** saved (`{safe_name}`, {rounds} round(s)).',
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /kvizgame list
    # ------------------------------------------------------------------

    @kvizgame.command(name='list', description='List available .siq packs')
    async def list_packs(self, interaction: discord.Interaction) -> None:
        """Show all uploaded .siq packs.

        Args:
            interaction: The Discord interaction.
        """
        packs = sorted(self._packs_dir.glob('*.siq'))
        if not packs:
            await interaction.response.send_message(
                'No packs uploaded yet.', ephemeral=True
            )
            return

        lines = [f'`{p.name}`' for p in packs]
        await interaction.response.send_message('\n'.join(lines), ephemeral=True)

    # ------------------------------------------------------------------
    # /kvizgame start
    # ------------------------------------------------------------------

    async def _pack_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Return pack names matching the current input."""
        names = [p.stem for p in sorted(self._packs_dir.glob('*.siq'))]
        return [
            app_commands.Choice(name=n, value=n)
            for n in names
            if current.lower() in n.lower()
        ][:25]

    @kvizgame.command(
        name='start', description='Start a KvizGame session for your voice channel'
    )
    @app_commands.describe(pack='Pack name to play')
    @app_commands.autocomplete(pack=_pack_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def start(self, interaction: discord.Interaction, pack: str) -> None:
        """Create a game session for all members currently in the caller's voice channel.

        Args:
            interaction: The Discord interaction.
            pack: Name of the .siq pack (without extension).
        """
        await interaction.response.defer()

        member = interaction.guild.get_member(interaction.user.id)
        if not member or not member.voice or not member.voice.channel:
            await interaction.followup.send(
                'You must be in a voice channel to start a game.'
            )
            return

        voice_channel = member.voice.channel
        channel_id = str(voice_channel.id)

        if channel_id in self._sessions:
            await interaction.followup.send(
                'A game is already running in that voice channel.'
            )
            return

        players = [m for m in voice_channel.members if not m.bot]
        if len(players) < 2:
            await interaction.followup.send(
                'Need at least 2 players in the voice channel.'
            )
            return

        siq_path = self._packs_dir / f'{pack}.siq'
        if not siq_path.exists():
            await interaction.followup.send(f'Pack `{pack}` not found.')
            return

        try:
            pkg = load(str(siq_path))
        except Exception as exc:
            await interaction.followup.send(f'Failed to load pack: {exc}')
            return

        player_ids = [str(m.id) for m in players]
        player_names = {str(m.id): m.display_name for m in players}

        try:
            game = GameMachine(pkg.package, player_ids, player_names, Settings())
        except ValueError as exc:
            await interaction.followup.send(str(exc))
            return

        session = GameSession(channel_id, game, str(siq_path))
        session.save()
        self._sessions[channel_id] = session
        self.logger.info(
            'KvizGame session started in channel %r with %d players',
            channel_id,
            len(players),
        )

        player_list = ', '.join(m.display_name for m in players)
        await interaction.followup.send(
            f'Game started in **{voice_channel.name}**'
            f' with pack **{pkg.package.name}**.\n'
            f'Players ({len(players)}): {player_list}'
        )

    # ------------------------------------------------------------------
    # /kvizgame stop
    # ------------------------------------------------------------------

    @kvizgame.command(
        name='stop', description='Stop the KvizGame session in your voice channel'
    )
    @app_commands.default_permissions(manage_guild=True)
    async def stop(self, interaction: discord.Interaction) -> None:
        """Stop an active KvizGame session.

        Args:
            interaction: The Discord interaction.
        """
        member = interaction.guild.get_member(interaction.user.id)
        if not member or not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                'You must be in a voice channel.', ephemeral=True
            )
            return

        channel_id = str(member.voice.channel.id)
        session = self._sessions.pop(channel_id, None)
        if session is None:
            await interaction.response.send_message(
                'No active game in your voice channel.', ephemeral=True
            )
            return

        session.delete_saved()
        self.logger.info('KvizGame session stopped in channel %r', channel_id)
        await interaction.response.send_message('Game stopped.')
