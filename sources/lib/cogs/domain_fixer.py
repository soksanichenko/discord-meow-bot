"""Domain fixer management cog"""

import discord
from discord import app_commands
from discord.ext import commands

from sources.lib.db.operations.domain_fixers import (
    DEFAULT_DOMAIN_FIXERS,
    delete_domain_fixer,
    get_all_domain_fixers,
    seed_default_domain_fixers,
    upsert_domain_fixer,
)
from sources.lib.db.operations.guilds import upsert_guild
from sources.lib.utils import Logger


class DomainFixerCog(commands.Cog):
    """Admin commands for managing guild-scoped URL domain fixer rules."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.logger = Logger()

    group = app_commands.Group(
        name='domain-fixer',
        description='Manage URL domain replacement rules',
        default_permissions=discord.Permissions(administrator=True),
    )

    @group.command(name='init', description='Load default domain replacement rules for this server')
    async def init_fixers(self, interaction: discord.Interaction) -> None:
        """Upsert the default domain fixer rules for this guild.

        Safe to run on a guild that already has rules — only the default
        domains are overwritten, custom rules for other domains are kept.

        Args:
            interaction: The Discord interaction context.
        """
        await upsert_guild(guild_id=interaction.guild_id, guild_name=interaction.guild.name)
        await seed_default_domain_fixers(guild_id=interaction.guild_id)
        self.logger.info('Default domain fixers seeded for guild %s', interaction.guild_id)
        names = ', '.join(f'`{f["source_domain"]}`' for f in DEFAULT_DOMAIN_FIXERS)
        await interaction.response.send_message(
            f'Default rules loaded: {names}',
            ephemeral=True,
        )

    @group.command(name='list', description='Show all configured domain replacement rules')
    async def list_fixers(self, interaction: discord.Interaction) -> None:
        """List all domain fixer rules for this guild.

        Args:
            interaction: The Discord interaction context.
        """
        fixers = await get_all_domain_fixers(guild_id=interaction.guild_id)
        if not fixers:
            await interaction.response.send_message('No domain fixer rules configured.', ephemeral=True)
            return

        lines = ['**Domain fixer rules:**']
        for fixer in fixers:
            subdomain_info = f' (subdomain: `{fixer.override_subdomain}`)' if fixer.override_subdomain else ''
            lines.append(f'• `{fixer.source_domain}` → `{fixer.replacement_domain}`{subdomain_info}')

        await interaction.response.send_message('\n'.join(lines), ephemeral=True)

    @group.command(name='add', description='Add or update a domain replacement rule')
    @app_commands.describe(
        source='Source domain to match, e.g. reddit.com',
        replacement='Replacement domain name, e.g. rxddit',
        subdomain='Override subdomain (leave empty to keep original)',
    )
    async def add_fixer(
        self,
        interaction: discord.Interaction,
        source: str,
        replacement: str,
        subdomain: str | None = None,
    ) -> None:
        """Add or update a domain fixer rule for this guild.

        Args:
            interaction: The Discord interaction context.
            source: Source domain to match.
            replacement: Domain name to replace with.
            subdomain: Optional subdomain override.
        """
        await upsert_guild(guild_id=interaction.guild_id, guild_name=interaction.guild.name)
        await upsert_domain_fixer(
            guild_id=interaction.guild_id,
            source_domain=source,
            replacement_domain=replacement,
            override_subdomain=subdomain or None,
        )
        self.logger.info('Domain fixer upserted: %s -> %s (guild %s)', source, replacement, interaction.guild_id)
        subdomain_info = f', subdomain override: `{subdomain}`' if subdomain else ''
        await interaction.response.send_message(
            f'Rule saved: `{source}` → `{replacement}`{subdomain_info}',
            ephemeral=True,
        )

    async def _source_domain_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete callback returning domain fixer source domains for this guild.

        Args:
            interaction: The Discord interaction context.
            current: The text the user has typed so far.

        Returns:
            Up to 25 matching domain choices.
        """
        fixers = await get_all_domain_fixers(guild_id=interaction.guild_id)
        return [
            app_commands.Choice(name=fixer.source_domain, value=fixer.source_domain)
            for fixer in fixers
            if current.lower() in fixer.source_domain.lower()
        ][:25]

    @group.command(name='remove', description='Remove a domain replacement rule')
    @app_commands.describe(source='Source domain to remove')
    @app_commands.autocomplete(source=_source_domain_autocomplete)
    async def remove_fixer(
        self,
        interaction: discord.Interaction,
        source: str,
    ) -> None:
        """Remove a domain fixer rule for this guild.

        Args:
            interaction: The Discord interaction context.
            source: Source domain to remove.
        """
        await delete_domain_fixer(guild_id=interaction.guild_id, source_domain=source)
        self.logger.info('Domain fixer removed: %s (guild %s)', source, interaction.guild_id)
        await interaction.response.send_message(
            f'Rule removed: `{source}`',
            ephemeral=True,
        )
