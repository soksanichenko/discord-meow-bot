"""Telegram relay cog — forward public Telegram channels to Discord via RSSHub."""

import asyncio
import re
from datetime import UTC, datetime
from html.parser import HTMLParser

import aiohttp
import discord
import feedparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from discord.ext import commands

from sources.config import config
from sources.lib.db.models import TelegramRelay
from sources.lib.db.operations.telegram_relay import (
    add_relay,
    get_all_relays,
    get_guild_relays,
    remove_relay,
    update_last_entry_id,
)
from sources.lib.utils import Logger

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
_TELEGRAM_COLOUR = discord.Colour(0x2CA5E0)


class _HtmlToMarkdown(HTMLParser):
    """Convert Telegram HTML formatting to Discord Markdown."""

    _WRAPPERS = {'b': '**', 'strong': '**', 'i': '*', 'em': '*', 's': '~~', 'code': '`'}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._href_stack: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == 'br':
            self._parts.append('\n')
        elif tag in ('p', 'pre', 'div'):
            pass
        elif tag == 'a':
            self._href_stack.append(attrs_dict.get('href') or '')
            self._parts.append('[')
        elif tag == 'img':
            src = attrs_dict.get('src') or ''
            if src:
                self.images.append(src)
        elif wrapper := self._WRAPPERS.get(tag):
            self._parts.append(wrapper)

    def handle_endtag(self, tag: str) -> None:
        if tag in ('p', 'pre', 'div'):
            self._parts.append('\n')
        elif tag == 'a':
            href = self._href_stack.pop() if self._href_stack else ''
            self._parts.append(f']({href})' if href else ']')
        elif wrapper := self._WRAPPERS.get(tag):
            self._parts.append(wrapper)

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def result(self) -> str:
        text = ''.join(self._parts).strip()
        return re.sub(r'\n{3,}', '\n\n', text)


def _html_to_markdown(html: str) -> tuple[str, list[str]]:
    """Convert Telegram HTML to Discord Markdown and extract image URLs.

    Returns:
        (markdown_text, image_urls)
    """
    parser = _HtmlToMarkdown()
    parser.feed(html)
    return parser.result(), parser.images


class TelegramRelayCog(commands.Cog):
    """Poll Telegram channels via RSSHub and forward new posts to Discord."""

    relay = app_commands.Group(
        name='telegram-relay',
        description='Forward public Telegram channels to Discord',
    )

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog.

        Args:
            bot: The Discord bot instance.
        """
        self.bot = bot
        self.logger = Logger()
        self._scheduler = AsyncIOScheduler()
        self._session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        """Open the HTTP session and start the polling scheduler."""
        self._session = aiohttp.ClientSession()
        interval = config.telegram_relay_poll_interval_minutes
        self._scheduler.add_job(
            self._poll_all,
            trigger='interval',
            minutes=interval,
            id='telegram_relay_poll',
            replace_existing=True,
        )
        self._scheduler.start()
        self.logger.info('Telegram relay scheduler started (every %d min)', interval)

    def cog_unload(self) -> None:
        """Stop the scheduler and close the HTTP session."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())

    # ------------------------------------------------------------------ commands

    @relay.command(
        name='add', description='Forward a public Telegram channel to a Discord channel'
    )
    @app_commands.describe(
        username='Telegram channel username (without @)',
        channel='Discord channel to post new messages to',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_add(
        self,
        interaction: discord.Interaction,
        username: str,
        channel: discord.TextChannel,
    ) -> None:
        """Add a Telegram → Discord relay for this guild.

        Fetches the RSS feed immediately to record the latest entry so existing
        posts are not flooded into the channel.

        Args:
            interaction: The Discord interaction.
            username: Telegram channel username without the @ prefix.
            channel: The Discord channel to receive new posts.
        """
        username = username.lstrip('@').lower()
        await interaction.response.defer(ephemeral=True)

        reachable, last_entry_id = await self._fetch_latest_entry_id(username)
        if not reachable:
            await interaction.followup.send(
                f'Could not reach the RSS feed for `@{username}`. '
                'Check that the channel is public and the username is correct.',
                ephemeral=True,
            )
            return

        inserted = await add_relay(
            guild_id=interaction.guild_id,
            tg_username=username,
            discord_channel_id=channel.id,
            last_entry_id=last_entry_id,
        )
        if not inserted:
            await interaction.followup.send(
                f'`@{username}` → {channel.mention} is already configured.',
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f'Now relaying `@{username}` to {channel.mention}. '
            f'New posts will appear within {config.telegram_relay_poll_interval_minutes} minutes.',
            ephemeral=True,
        )
        self.logger.info(
            'Relay added: @%s → #%s (guild %d)',
            username,
            channel.name,
            interaction.guild_id,
        )

    @relay.command(name='remove', description='Stop forwarding a Telegram channel')
    @app_commands.describe(username='Telegram channel username to remove (without @)')
    @app_commands.default_permissions(manage_guild=True)
    async def relay_remove(
        self,
        interaction: discord.Interaction,
        username: str,
    ) -> None:
        """Remove a Telegram relay for this guild.

        Args:
            interaction: The Discord interaction.
            username: Telegram channel username to stop relaying.
        """
        username = username.lstrip('@').lower()
        deleted = await remove_relay(interaction.guild_id, username)
        if not deleted:
            await interaction.response.send_message(
                f'No relay found for `@{username}`.',
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f'Relay for `@{username}` removed.',
            ephemeral=True,
        )
        self.logger.info(
            'Relay removed: @%s (guild %d)', username, interaction.guild_id
        )

    @relay.command(
        name='list', description='Show all active Telegram relays for this server'
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_list(self, interaction: discord.Interaction) -> None:
        """List all Telegram relays configured for this guild.

        Args:
            interaction: The Discord interaction.
        """
        relays = await get_guild_relays(interaction.guild_id)
        if not relays:
            await interaction.response.send_message(
                'No Telegram relays configured. Use `/telegram-relay add` to set one up.',
                ephemeral=True,
            )
            return

        embed = discord.Embed(title='Telegram Relays', colour=discord.Colour.blue())
        lines = []
        for r in relays:
            ch = interaction.guild.get_channel(r.discord_channel_id)
            ch_mention = ch.mention if ch else f'<#{r.discord_channel_id}>'
            lines.append(f'`@{r.tg_username}` → {ch_mention}')
        embed.description = '\n'.join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ polling

    async def _poll_all(self) -> None:
        """Poll every configured relay and forward new entries to Discord."""
        relays = await get_all_relays()
        for entry in relays:
            try:
                await self._poll_relay(entry)
            except Exception:
                self.logger.exception(
                    'Unexpected error polling relay @%s', entry.tg_username
                )

    async def _poll_relay(self, relay: TelegramRelay) -> None:
        """Fetch the RSS feed for one relay and post new entries.

        Args:
            relay: The TelegramRelay row to process.
        """
        url = f'{config.rsshub_url}/telegram/channel/{relay.tg_username}'
        try:
            async with self._session.get(url, timeout=_REQUEST_TIMEOUT) as resp:
                content = await resp.text()
        except Exception as exc:
            self.logger.warning(
                'Failed to fetch RSS for @%s: %s', relay.tg_username, exc
            )
            return

        feed = feedparser.parse(content)
        self.logger.info(
            'Polling @%s: %d entries in feed, last_entry_id=%r',
            relay.tg_username,
            len(feed.entries),
            relay.last_entry_id,
        )
        if not feed.entries:
            return

        new_entries = []
        for entry in feed.entries:
            if entry.get('id') == relay.last_entry_id:
                break
            new_entries.append(entry)

        self.logger.info(
            'Polling @%s: %d new entry(ies) found', relay.tg_username, len(new_entries)
        )
        if not new_entries:
            return

        channel = self.bot.get_channel(relay.discord_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(relay.discord_channel_id)
            except discord.NotFound:
                self.logger.warning(
                    'Channel %d not found for relay @%s',
                    relay.discord_channel_id,
                    relay.tg_username,
                )
                return

        raw_title = feed.feed.get('title') or ''
        channel_title = (
            re.sub(r'\s*-\s*Telegram Channel$', '', raw_title).strip() or None
        )

        # Post oldest-first so the channel reads chronologically.
        for entry in reversed(new_entries):
            embeds = self._build_embeds(entry, relay.tg_username, channel_title)
            try:
                await channel.send(embeds=embeds)
            except discord.Forbidden:
                self.logger.warning(
                    'No permission to post in channel %d for relay @%s',
                    relay.discord_channel_id,
                    relay.tg_username,
                )
                return

        await update_last_entry_id(relay.id, feed.entries[0]['id'])
        self.logger.info(
            'Relayed %d new post(s) from @%s', len(new_entries), relay.tg_username
        )

    async def _fetch_latest_entry_id(self, username: str) -> tuple[bool, str | None]:
        """Fetch the RSS feed and return reachability + latest entry ID.

        Args:
            username: Telegram channel username.

        Returns:
            (reachable, latest_entry_id). reachable is False only on network/parse
            failure. latest_entry_id is None when the channel exists but has no posts.
        """
        url = f'{config.rsshub_url}/telegram/channel/{username}'
        try:
            async with self._session.get(url, timeout=_REQUEST_TIMEOUT) as resp:
                if resp.status >= 400:
                    self.logger.warning(
                        'RSS fetch @%s returned HTTP %d', username, resp.status
                    )
                    return False, None
                content = await resp.text()
        except Exception as exc:
            self.logger.warning('Failed to fetch RSS for @%s on add: %s', username, exc)
            return False, None

        feed = feedparser.parse(content)
        if feed.bozo and not feed.entries:
            self.logger.warning(
                'RSS feed @%s is malformed: %s', username, feed.get('bozo_exception')
            )
            return False, None
        latest = feed.entries[0].get('id') if feed.entries else None
        return True, latest

    @staticmethod
    def _build_embeds(
        entry: feedparser.FeedParserDict,
        username: str,
        channel_title: str | None = None,
    ) -> list[discord.Embed]:
        """Build Discord embeds for a single RSS entry.

        Mimics Discord's native Telegram link preview: author line shows
        'Telegram', title is the channel name and links to the post,
        thumbnail sits to the right of the text.

        Args:
            entry: A feedparser entry dict.
            username: Telegram channel username (used in footer and as title fallback).
            channel_title: Display name of the Telegram channel from the RSS feed.

        Returns:
            List of Discord Embeds (always at least one).
        """
        text, images = _html_to_markdown(entry.get('summary') or '')
        text = text.replace('Video is too big', '').strip()
        link = entry.get('link') or None

        # Fallback: enclosures and media extensions when no <img> in HTML
        if not images:
            for enc in entry.get('enclosures', []):
                if enc.get('type', '').startswith('image/'):
                    images.append(enc['url'])
            for thumb in entry.get('media_thumbnail', []):
                if url := thumb.get('url'):
                    images.append(url)

        main = discord.Embed(
            title=channel_title or f'@{username}',
            description=text[:4096] or None,
            url=link,
            colour=_TELEGRAM_COLOUR,
        )
        main.set_author(name='Telegram')
        main.set_footer(text=f'@{username}')

        published = entry.get('published_parsed')
        if published:
            main.timestamp = datetime(*published[:6], tzinfo=UTC)

        if images:
            main.set_thumbnail(url=images[0])

        embeds = [main]
        for img_url in images[1:10]:
            extra = discord.Embed(url=link, colour=_TELEGRAM_COLOUR)
            extra.set_image(url=img_url)
            embeds.append(extra)

        return embeds
