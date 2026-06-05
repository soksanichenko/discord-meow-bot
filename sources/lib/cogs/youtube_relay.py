"""YouTube relay cog — forward YouTube channel uploads to Discord via RSS."""

import asyncio
import time
import urllib.parse

import aiohttp
import discord
import feedparser
from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from discord.ext import commands

from sources.config import config
from sources.lib.cogs.relay_utils import (
    build_relay_choices,
    parse_relay_id,
    resolve_channel,
)
from sources.lib.db.models import YouTubeRelay
from sources.lib.db.operations.youtube_live_session import (
    add_live_session,
    get_all_live_sessions,
    remove_live_session,
)
from sources.lib.db.operations.youtube_relay import (
    add_relay,
    enable_relay_type,
    get_all_relays,
    get_guild_relays,
    get_relay_by_id,
    remove_relay_by_id,
    set_relay_message_by_id,
    update_last_video_id,
    update_relay_content_flags,
)
from sources.lib.utils.logger import Logger
from sources.lib.utils.metrics import (
    api_call_latency,
    relay_fetch_errors,
    relay_last_poll,
    relay_posts,
    scheduler_job_failures,
)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
_YT_API_CHANNELS = 'https://www.googleapis.com/youtube/v3/channels'
_YT_API_VIDEOS = 'https://www.googleapis.com/youtube/v3/videos'
_YT_RSS_BASE = 'https://www.youtube.com/feeds/videos.xml'

_SEEN_WINDOW = 15  # matches the max number of entries in a YouTube RSS feed

# Ordered list of (flag_key, display_label) for all content types.
_CONTENT_TYPES: list[tuple[str, str]] = [
    ('post_videos', 'Videos'),
    ('post_shorts', 'Shorts'),
    ('post_lives', 'Lives'),
]


def _content_types_label(post_videos: bool, post_shorts: bool, post_lives: bool) -> str:
    """Build a human-readable label from content type flags.

    Args:
        post_videos: Whether regular videos are included.
        post_shorts: Whether Shorts are included.
        post_lives: Whether live streams are included.

    Returns:
        Comma-separated label, e.g. 'videos, shorts'.
    """
    flags = {
        'post_videos': post_videos,
        'post_shorts': post_shorts,
        'post_lives': post_lives,
    }
    return ', '.join(label.lower() for key, label in _CONTENT_TYPES if flags[key])


class _RelayView(discord.ui.View):
    """Base class for relay configuration views.

    Stores the original interaction for timeout cleanup and provides a helper
    to disable all child components.
    """

    def __init__(self, interaction: discord.Interaction, timeout: float = 180) -> None:
        """Initialise the view.

        Args:
            interaction: The original interaction (used to edit the message on timeout).
            timeout: View timeout in seconds.
        """
        super().__init__(timeout=timeout)
        self._interaction = interaction

    def _disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    async def on_timeout(self) -> None:
        """Disable all components when the view times out."""
        self._disable_all()
        try:
            await self._interaction.edit_original_response(view=self)
        except discord.HTTPException:
            pass


class _RelaySetupView(_RelayView):
    """Per-type channel selector shown after resolving a YouTube channel."""

    def __init__(
        self,
        interaction: discord.Interaction,
        yt_channel_id: str,
        yt_channel_title: str,
        last_video_id: str | None,
    ) -> None:
        """Initialise the view.

        Args:
            interaction: The original deferred interaction.
            yt_channel_id: Resolved YouTube channel ID (UCxxx).
            yt_channel_title: Display name of the YouTube channel.
            last_video_id: Latest video ID to use as the relay sentinel.
        """
        super().__init__(interaction)
        self.yt_channel_id = yt_channel_id
        self.yt_channel_title = yt_channel_title
        self.last_video_id = last_video_id
        self._new: dict[str, int | None] = {key: None for key, _ in _CONTENT_TYPES}

        for row, (flag_key, label) in enumerate(_CONTENT_TYPES, start=1):
            select = discord.ui.ChannelSelect(
                placeholder=f'{label} → Discord channel (leave empty to skip)',
                min_values=0,
                max_values=1,
                channel_types=[discord.ChannelType.text],
                row=row,
            )
            select.callback = self._make_callback(flag_key, select)
            self.add_item(select)

        confirm = discord.ui.Button(
            label='Confirm', style=discord.ButtonStyle.green, row=4
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

    def _make_callback(self, flag_key: str, select: discord.ui.ChannelSelect):
        async def callback(interaction: discord.Interaction) -> None:
            self._new[flag_key] = select.values[0].id if select.values else None
            await interaction.response.defer()

        return callback

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        """Create relay rows and replace the message with a summary.

        Types targeting the same Discord channel are merged into one relay row.

        Args:
            interaction: The component interaction.
        """
        if not any(self._new.values()):
            await interaction.response.send_message(
                'Select at least one Discord channel before confirming.',
                ephemeral=True,
            )
            return

        # Merge types sharing the same Discord channel into one relay row.
        channel_flags: dict[int, dict[str, bool]] = {}
        for flag_key, _ in _CONTENT_TYPES:
            ch_id = self._new[flag_key]
            if ch_id is None:
                continue
            if ch_id not in channel_flags:
                channel_flags[ch_id] = {k: False for k, _ in _CONTENT_TYPES}
            channel_flags[ch_id][flag_key] = True

        added, skipped = 0, 0
        for discord_channel_id, flags in channel_flags.items():
            inserted = await add_relay(
                guild_id=interaction.guild_id,
                yt_channel_id=self.yt_channel_id,
                yt_channel_title=self.yt_channel_title,
                discord_channel_id=discord_channel_id,
                last_video_id=self.last_video_id,
                **flags,
            )
            if inserted:
                added += 1
            else:
                skipped += 1

        lines = []
        for discord_channel_id, flags in channel_flags.items():
            ch = interaction.guild.get_channel(discord_channel_id)
            ch_mention = ch.mention if ch else f'<#{discord_channel_id}>'
            types = _content_types_label(**flags)
            lines.append(f'{ch_mention} — {types}')

        note = (
            f'\n{skipped} relay(s) already existed and were skipped.' if skipped else ''
        )
        self._disable_all()
        await interaction.response.edit_message(
            content=(
                f'Relay configured for **{self.yt_channel_title}**:\n'
                + '\n'.join(lines)
                + note
            ),
            view=self,
        )


class _RelayModifyView(_RelayView):
    """Per-type channel selector for modifying an existing relay configuration."""

    def __init__(
        self,
        interaction: discord.Interaction,
        relays: list[YouTubeRelay],
    ) -> None:
        """Initialise the view with ChannelSelects pre-filled from the current config.

        Args:
            interaction: The original interaction.
            relays: All relay rows for the YouTube channel being modified.
        """
        super().__init__(interaction)
        self._relays = relays

        # Current state: flag_key → (relay_id, discord_channel_id) or None.
        self._current: dict[str, tuple[int, int] | None] = {
            k: None for k, _ in _CONTENT_TYPES
        }
        for r in relays:
            for flag_key, _ in _CONTENT_TYPES:
                if getattr(r, flag_key):
                    self._current[flag_key] = (r.id, r.discord_channel_id)

        # Mutable new selections; start equal to current.
        self._new: dict[str, int | None] = {
            k: v[1] if v else None for k, v in self._current.items()
        }

        for row, (flag_key, label) in enumerate(_CONTENT_TYPES, start=1):
            ch_id = self._current[flag_key][1] if self._current[flag_key] else None
            select = discord.ui.ChannelSelect(
                placeholder=f'{label} → Discord channel (empty to stop)',
                min_values=0,
                max_values=1,
                channel_types=[discord.ChannelType.text],
                default_values=[discord.Object(id=ch_id)] if ch_id else [],
                row=row,
            )
            select.callback = self._make_callback(flag_key, select)
            self.add_item(select)

        save = discord.ui.Button(label='Save', style=discord.ButtonStyle.green, row=4)
        save.callback = self._on_confirm
        self.add_item(save)

    def _make_callback(self, flag_key: str, select: discord.ui.ChannelSelect):
        async def callback(interaction: discord.Interaction) -> None:
            self._new[flag_key] = select.values[0].id if select.values else None
            await interaction.response.defer()

        return callback

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        """Apply changes: update/delete existing relay rows, enable types on new channels.

        Pass 1 — for each relay row, disable flags whose target channel changed.
        Pass 2 — enable moved flags on their new Discord channels (creating rows as needed,
        inheriting last_video_id so history is not replayed).

        Args:
            interaction: The component interaction.
        """
        relay = self._relays[0]
        guild_id = interaction.guild_id
        latest_video_id = next(
            (r.last_video_id for r in self._relays if r.last_video_id), None
        )

        for r in self._relays:
            new_flags = {
                flag_key: getattr(r, flag_key)
                and self._new[flag_key] == r.discord_channel_id
                for flag_key, _ in _CONTENT_TYPES
            }
            old_flags = {
                flag_key: getattr(r, flag_key) for flag_key, _ in _CONTENT_TYPES
            }
            if new_flags == old_flags:
                continue
            if not any(new_flags.values()):
                await remove_relay_by_id(r.id)
            else:
                await update_relay_content_flags(r.id, **new_flags)

        for flag_key, _ in _CONTENT_TYPES:
            old_ch = self._current[flag_key][1] if self._current[flag_key] else None
            new_ch = self._new[flag_key]
            if new_ch is None or new_ch == old_ch:
                continue
            await enable_relay_type(
                guild_id,
                relay.yt_channel_id,
                relay.yt_channel_title,
                new_ch,
                flag_key,
                latest_video_id,
            )

        self._disable_all()
        await interaction.response.edit_message(
            content=f'**{relay.yt_channel_title}** relay updated.',
            view=self,
        )


class _RelayRemoveView(_RelayView):
    """Type-based removal view — lets the user pick which content types to stop relaying."""

    def __init__(
        self,
        interaction: discord.Interaction,
        relays: list[YouTubeRelay],
    ) -> None:
        """Initialise the view.

        Args:
            interaction: The original interaction.
            relays: All relay rows for the YouTube channel being configured.
        """
        super().__init__(interaction, timeout=60)
        self._relays = relays
        self._selected: set[str] = set()

        active_options = [
            discord.SelectOption(label=label, value=flag_key)
            for flag_key, label in _CONTENT_TYPES
            if any(getattr(r, flag_key) for r in relays)
        ]
        self.type_select = discord.ui.Select(
            placeholder='Select content types to remove',
            min_values=1,
            max_values=len(active_options),
            options=active_options,
        )
        self.type_select.callback = self._on_select
        self.add_item(self.type_select)

        remove = discord.ui.Button(label='Remove', style=discord.ButtonStyle.danger)
        remove.callback = self._on_confirm
        self.add_item(remove)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self._selected = set(self.type_select.values)
        await interaction.response.defer()

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        """Apply removal: update or delete relay rows based on remaining active types.

        Args:
            interaction: The component interaction.
        """
        if not self._selected:
            await interaction.response.send_message(
                'Select at least one content type first.',
                ephemeral=True,
            )
            return

        for relay in self._relays:
            new_flags = {
                flag_key: getattr(relay, flag_key) and flag_key not in self._selected
                for flag_key, _ in _CONTENT_TYPES
            }
            old_flags = {
                flag_key: getattr(relay, flag_key) for flag_key, _ in _CONTENT_TYPES
            }
            if new_flags == old_flags:
                continue
            if not any(new_flags.values()):
                await remove_relay_by_id(relay.id)
            else:
                await update_relay_content_flags(relay.id, **new_flags)

        removed_labels = [
            label for flag_key, label in _CONTENT_TYPES if flag_key in self._selected
        ]
        self._disable_all()
        await interaction.response.edit_message(
            content=(
                f'Removed **{", ".join(removed_labels)}** '
                f'from **{self._relays[0].yt_channel_title}**.'
            ),
            view=self,
        )


class _SetMessageModal(discord.ui.Modal):
    """Modal for editing a relay's custom notification message."""

    def __init__(self, relay: YouTubeRelay, kind: str, kind_label: str) -> None:
        """Initialise the modal.

        Args:
            relay: The relay whose message is being edited.
            kind: Content type key ('video', 'short', or 'live').
            kind_label: Human-readable label shown in the title.
        """
        super().__init__(title=f'Set {kind_label} message')
        self.relay = relay
        self.kind = kind

        field_map = {
            'video': relay.message_video,
            'short': relay.message_short,
            'live': relay.message_live,
        }
        self.message_input = discord.ui.TextInput(
            label='Notification message',
            style=discord.TextStyle.paragraph,
            default=field_map[kind] or '',
            max_length=500,
            required=True,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Save the edited message on modal submit.

        Args:
            interaction: The Discord interaction from the modal submission.
        """
        text = self.message_input.value.strip() or None
        await set_relay_message_by_id(self.relay.id, self.kind, text)
        label = 'reset to default' if text is None else 'updated'
        await interaction.response.send_message(
            f'Custom {self.kind} message for **{self.relay.yt_channel_title}** {label}.',
            ephemeral=True,
        )


class YouTubeRelayCog(commands.Cog):
    """Poll YouTube channels via RSS and forward new videos to Discord."""

    relay = app_commands.Group(
        name='youtube-relay',
        description='Forward YouTube channel uploads to Discord',
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
        interval = config.youtube_relay_poll_interval_minutes
        self._scheduler.add_job(
            self._poll_all,
            trigger='interval',
            minutes=interval,
            id='youtube_relay_poll',
            replace_existing=True,
        )
        self._scheduler.add_listener(
            lambda e: scheduler_job_failures.labels(job=e.job_id).inc(),
            EVENT_JOB_ERROR,
        )
        self._scheduler.start()
        self.logger.info('YouTube relay scheduler started (every %d min)', interval)

    def cog_unload(self) -> None:
        """Stop the scheduler and close the HTTP session."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())

    # ------------------------------------------------------------------ autocomplete

    async def _yt_channel_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Return one deduplicated choice per configured YouTube channel.

        Used by remove and modify, where the user picks a YouTube channel first.
        """
        relays = await get_guild_relays(interaction.guild_id)
        seen: set[str] = set()
        choices = []
        for r in relays:
            if r.yt_channel_id in seen:
                continue
            seen.add(r.yt_channel_id)
            if current.lower() in r.yt_channel_title.lower():
                choices.append(
                    app_commands.Choice(name=r.yt_channel_title, value=r.yt_channel_id)
                )
        return choices[:25]

    async def _relay_channel_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Return one choice per relay row (value = relay ID).

        When a YouTube channel has multiple Discord targets, each is listed with
        the Discord channel name in parentheses to disambiguate.
        """
        relays = await get_guild_relays(interaction.guild_id)
        return build_relay_choices(
            relays,
            current,
            interaction.guild,
            get_name=lambda r: r.yt_channel_title,
            get_key=lambda r: r.yt_channel_id,
        )

    # ------------------------------------------------------------------ commands

    @relay.command(
        name='add',
        description="Forward a YouTube channel's uploads to a Discord channel",
    )
    @app_commands.describe(
        channel='YouTube channel URL, @handle, or channel ID (UCxxx)'
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_add(
        self,
        interaction: discord.Interaction,
        channel: str,
    ) -> None:
        """Add a YouTube → Discord relay for this guild.

        Resolves the channel via the YouTube API, then shows a per-type channel
        selector so videos, Shorts, and live streams can route to different
        Discord channels.

        Args:
            interaction: The Discord interaction.
            channel: YouTube channel URL, @handle, or UCxxx channel ID.
        """
        if not config.youtube_api_key:
            await interaction.response.send_message(
                'YouTube API key is not configured. Set the `YOUTUBE_API_KEY` environment variable.',
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        resolved = await self._resolve_channel(channel)
        if resolved is None:
            await interaction.followup.send(
                f'Could not find a YouTube channel for `{channel}`. '
                'Accepted formats: channel URL, `@handle`, or `UCxxx` channel ID.',
                ephemeral=True,
            )
            return

        yt_channel_id, yt_channel_title = resolved
        reachable, last_video_id = await self._fetch_latest_video_id(yt_channel_id)
        if not reachable:
            await interaction.followup.send(
                f'Could not reach the RSS feed for **{yt_channel_title}**. Please try again.',
                ephemeral=True,
            )
            return

        view = _RelaySetupView(
            interaction, yt_channel_id, yt_channel_title, last_video_id
        )
        await interaction.followup.send(
            f'**{yt_channel_title}** — select a Discord channel for each content type.\n'
            'Leave a type empty to not forward it.',
            view=view,
            ephemeral=True,
        )
        self.logger.info(
            'Relay setup started: %s (%s) (guild %d)',
            yt_channel_title,
            yt_channel_id,
            interaction.guild_id,
        )

    @relay.command(name='remove', description='Stop forwarding a YouTube channel')
    @app_commands.describe(channel='YouTube channel to remove')
    @app_commands.autocomplete(channel=_yt_channel_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def relay_remove(
        self,
        interaction: discord.Interaction,
        channel: str,
    ) -> None:
        """Remove relay types for a YouTube channel in this guild.

        Shows a content-type selector (Videos / Shorts / Lives). Relay rows with
        no remaining active types after removal are deleted automatically.

        Args:
            interaction: The Discord interaction.
            channel: YouTube channel ID (UCxxx) from autocomplete.
        """
        all_relays = await get_guild_relays(interaction.guild_id)
        relays = [r for r in all_relays if r.yt_channel_id == channel]

        if not relays:
            await interaction.response.send_message(
                'No relay found for this channel.',
                ephemeral=True,
            )
            return

        summary = self._routing_summary(relays, interaction.guild)
        view = _RelayRemoveView(interaction, relays)
        await interaction.response.send_message(
            f'**{relays[0].yt_channel_title}** — current routing:\n{summary}\n\nSelect the content types to stop relaying:',
            view=view,
            ephemeral=True,
        )

    @relay.command(
        name='list', description='Show all active YouTube relays for this server'
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_list(self, interaction: discord.Interaction) -> None:
        """List all YouTube relays configured for this guild.

        Args:
            interaction: The Discord interaction.
        """
        relays = await get_guild_relays(interaction.guild_id)
        if not relays:
            await interaction.response.send_message(
                'No YouTube relays configured. Use `/youtube-relay add` to set one up.',
                ephemeral=True,
            )
            return

        grouped: dict[str, list[YouTubeRelay]] = {}
        for r in relays:
            grouped.setdefault(r.yt_channel_id, []).append(r)

        sections = []
        for yt_channel_id, channel_relays in grouped.items():
            yt_url = f'https://www.youtube.com/channel/{yt_channel_id}'
            title = channel_relays[0].yt_channel_title
            lines = [f'**[{title}]({yt_url})**']
            for r in channel_relays:
                ch = interaction.guild.get_channel(r.discord_channel_id)
                ch_mention = ch.mention if ch else f'<#{r.discord_channel_id}>'
                types = _content_types_label(r.post_videos, r.post_shorts, r.post_lives)
                lines.append(f'{ch_mention} — {types}')
            sections.append('\n'.join(lines))

        embed = discord.Embed(
            description='\n\n'.join(sections), colour=discord.Colour.red()
        )
        embed.set_author(
            name='YouTube Relays',
            icon_url='https://www.gstatic.com/youtube/img/branding/favicon/favicon_96x96.png',
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @relay.command(
        name='modify',
        description='Change the Discord channel routing for a YouTube relay',
    )
    @app_commands.describe(channel='YouTube channel to modify')
    @app_commands.autocomplete(channel=_yt_channel_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def relay_modify(
        self,
        interaction: discord.Interaction,
        channel: str,
    ) -> None:
        """Modify per-type Discord channel routing for an existing relay.

        Shows a channel selector pre-filled with the current configuration.
        Moving a type to a new channel updates the relay rows; clearing a type
        disables it (and deletes the row if no types remain).

        Args:
            interaction: The Discord interaction.
            channel: YouTube channel ID (UCxxx) from autocomplete.
        """
        all_relays = await get_guild_relays(interaction.guild_id)
        relays = [r for r in all_relays if r.yt_channel_id == channel]

        if not relays:
            await interaction.response.send_message(
                'No relay found for this channel.',
                ephemeral=True,
            )
            return

        summary = self._routing_summary(relays, interaction.guild)
        view = _RelayModifyView(interaction, relays)
        await interaction.response.send_message(
            f'**{relays[0].yt_channel_title}** — current routing:\n{summary}\n\nUse the dropdowns below to update each type.',
            view=view,
            ephemeral=True,
        )

    @relay.command(
        name='set-message',
        description='Edit the custom notification message for a YouTube relay',
    )
    @app_commands.describe(
        channel='YouTube channel to configure',
        kind='Content type to customise',
    )
    @app_commands.autocomplete(channel=_relay_channel_autocomplete)
    @app_commands.choices(
        kind=[
            app_commands.Choice(name='Video', value='video'),
            app_commands.Choice(name='Short', value='short'),
            app_commands.Choice(name='Live', value='live'),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_set_message(
        self,
        interaction: discord.Interaction,
        channel: str,
        kind: app_commands.Choice[str],
    ) -> None:
        """Open a modal to edit the custom notification message for a relay.

        Args:
            interaction: The Discord interaction.
            channel: Relay ID from autocomplete.
            kind: Content type (video, short, or live).
        """
        relay_id = await parse_relay_id(channel, interaction)
        if relay_id is None:
            return

        relay = await get_relay_by_id(relay_id)
        if relay is None or relay.guild_id != interaction.guild_id:
            await interaction.response.send_message(
                'Relay not found. Add one first with `/youtube-relay add`.',
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            _SetMessageModal(relay, kind.value, kind.name)
        )

    @relay.command(
        name='remove-message',
        description='Reset the notification message for a YouTube relay to the default',
    )
    @app_commands.describe(
        channel='YouTube channel to configure',
        kind='Content type to reset',
    )
    @app_commands.autocomplete(channel=_relay_channel_autocomplete)
    @app_commands.choices(
        kind=[
            app_commands.Choice(name='Video', value='video'),
            app_commands.Choice(name='Short', value='short'),
            app_commands.Choice(name='Live', value='live'),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_remove_message(
        self,
        interaction: discord.Interaction,
        channel: str,
        kind: app_commands.Choice[str],
    ) -> None:
        """Reset a relay's custom message back to the built-in default.

        Args:
            interaction: The Discord interaction.
            channel: Relay ID from autocomplete.
            kind: Content type whose message to reset (video, short, or live).
        """
        relay_id = await parse_relay_id(channel, interaction)
        if relay_id is None:
            return

        title = await set_relay_message_by_id(relay_id, kind.value, None)
        if title is None:
            await interaction.response.send_message(
                'Relay not found.',
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f'{kind.name} message for **{title}** reset to default.',
            ephemeral=True,
        )

    # ------------------------------------------------------------------ helpers

    async def _resolve_channel(self, raw: str) -> tuple[str, str] | None:
        """Resolve a YouTube channel input to (channel_id, channel_title).

        Accepts a YouTube URL (channel or handle path), a bare @handle, a bare
        handle name, or a UCxxx channel ID.

        Args:
            raw: User-provided channel identifier.

        Returns:
            (yt_channel_id, yt_channel_title) or None if not found or API error.
        """
        raw = raw.strip()
        _url = raw if '://' in raw else f'https://{raw}'
        parsed = urllib.parse.urlparse(_url)
        netloc = parsed.netloc

        if netloc == 'youtube.com' or netloc.endswith('.youtube.com'):
            path = parsed.path.rstrip('/')
            parts = path.split('/')
            if len(parts) >= 3 and parts[1] == 'channel':
                identifier = parts[2]
            elif len(parts) >= 2 and parts[1].startswith('@'):
                identifier = parts[1][1:]
            else:
                identifier = parts[-1].lstrip('@')
        else:
            identifier = raw.lstrip('@')

        if not identifier:
            return None

        if identifier.startswith('UC'):
            params = {
                'part': 'snippet',
                'id': identifier,
                'key': config.youtube_api_key,
            }
        else:
            params = {
                'part': 'snippet',
                'forHandle': identifier,
                'key': config.youtube_api_key,
            }

        try:
            with api_call_latency.labels(service='youtube').time():
                async with self._session.get(
                    _YT_API_CHANNELS, params=params, timeout=_REQUEST_TIMEOUT
                ) as resp:
                    if resp.status != 200:
                        self.logger.warning(
                            'YouTube API returned HTTP %d for %r', resp.status, raw
                        )
                        return None
                    data = await resp.json()
        except Exception as exc:
            self.logger.warning('YouTube API request failed for %r: %s', raw, exc)
            return None

        items = data.get('items', [])
        if not items:
            return None
        item = items[0]
        return item['id'], item['snippet']['title']

    async def _fetch_latest_video_id(
        self, yt_channel_id: str
    ) -> tuple[bool, str | None]:
        """Fetch the RSS feed and return reachability + latest video ID.

        Args:
            yt_channel_id: YouTube channel ID (UCxxx).

        Returns:
            (reachable, latest_video_id). reachable is False only on network/HTTP
            failure. latest_video_id is None when the channel exists but has no videos.
        """
        url = f'{_YT_RSS_BASE}?channel_id={yt_channel_id}'
        try:
            async with self._session.get(url, timeout=_REQUEST_TIMEOUT) as resp:
                if resp.status >= 400:
                    self.logger.warning(
                        'RSS fetch %s returned HTTP %d', yt_channel_id, resp.status
                    )
                    return False, None
                content = await resp.text()
        except Exception as exc:
            self.logger.warning('Failed to fetch RSS for %s: %s', yt_channel_id, exc)
            return False, None
        feed = feedparser.parse(content)
        if not feed.entries:
            return True, None
        return True, self._video_id_from_entry(feed.entries[0])

    @staticmethod
    def _video_id_from_entry(entry: feedparser.FeedParserDict) -> str | None:
        """Extract the YouTube video ID from a feedparser entry.

        Args:
            entry: A feedparser entry from the YouTube Atom feed.

        Returns:
            Video ID string or None.
        """
        yt_id = entry.get('yt_videoid')
        if yt_id:
            return yt_id
        raw_id = entry.get('id', '')
        if raw_id.startswith('yt:video:'):
            return raw_id[len('yt:video:') :]
        return None

    async def _classify_video(self, video_id: str) -> tuple[bool, bool]:
        """Determine whether a video is a Short and/or a live stream.

        Runs both checks concurrently. Defaults to (False, False) on any error
        so that classification failures never silently drop videos.

        Args:
            video_id: YouTube video ID.

        Returns:
            (is_short, is_live).
        """

        async def check_short() -> bool:
            try:
                async with self._session.head(
                    f'https://www.youtube.com/shorts/{video_id}',
                    allow_redirects=False,
                    timeout=_REQUEST_TIMEOUT,
                ) as resp:
                    return resp.status == 200
            except Exception:
                return False

        async def check_live() -> bool:
            try:
                with api_call_latency.labels(service='youtube').time():
                    async with self._session.get(
                        _YT_API_VIDEOS,
                        params={
                            'part': 'liveStreamingDetails',
                            'id': video_id,
                            'key': config.youtube_api_key,
                        },
                        timeout=_REQUEST_TIMEOUT,
                    ) as resp:
                        data = await resp.json()
                items = data.get('items', [])
                return bool(items and items[0].get('liveStreamingDetails'))
            except Exception:
                return False

        is_short, is_live = await asyncio.gather(check_short(), check_live())
        return is_short, is_live

    @staticmethod
    def _notification_message(
        relay: YouTubeRelay, is_short: bool, is_live: bool
    ) -> str:
        """Return the notification message for a new video.

        Args:
            relay: The relay whose custom messages are checked first.
            is_short: True if the video is a YouTube Short.
            is_live: True if the video is a live stream.

        Returns:
            Message text to prepend before the video URL.
        """
        if is_live:
            return (
                relay.message_live or f'**{relay.yt_channel_title}** is streaming now'
            )
        if is_short:
            return relay.message_short or f'New short from **{relay.yt_channel_title}**'
        return relay.message_video or f'New video from **{relay.yt_channel_title}**'

    @staticmethod
    def _routing_summary(relays: list[YouTubeRelay], guild: discord.Guild) -> str:
        """Build a human-readable per-type routing summary for a set of relay rows.

        Args:
            relays: All relay rows for one YouTube channel.
            guild: The Discord guild, used to resolve channel mentions.

        Returns:
            Multi-line string, one line per content type, e.g. 'Videos → #general'.
        """
        lines = []
        for flag_key, label in _CONTENT_TYPES:
            ch_id = next(
                (r.discord_channel_id for r in relays if getattr(r, flag_key)), None
            )
            if ch_id:
                ch = guild.get_channel(ch_id)
                ch_str = ch.mention if ch else f'<#{ch_id}>'
            else:
                ch_str = '*not configured*'
            lines.append(f'{label} → {ch_str}')
        return '\n'.join(lines)

    async def _fetch_live_embed_data(
        self, video_id: str, yt_channel_id: str
    ) -> tuple[int | None, str | None, str | None]:
        """Fetch viewer count, channel icon, and thumbnail for a live stream embed.

        Both API calls run concurrently. Any value may be None on error.

        Args:
            video_id: YouTube video ID.
            yt_channel_id: YouTube channel ID (UCxxx).

        Returns:
            (viewer_count, channel_icon_url, thumbnail_url).
        """

        async def get_video_data() -> tuple[int | None, str | None]:
            try:
                with api_call_latency.labels(service='youtube').time():
                    async with self._session.get(
                        _YT_API_VIDEOS,
                        params={
                            'part': 'snippet,liveStreamingDetails',
                            'id': video_id,
                            'key': config.youtube_api_key,
                        },
                        timeout=_REQUEST_TIMEOUT,
                    ) as resp:
                        data = await resp.json()
                items = data.get('items', [])
                if not items:
                    return None, None
                item = items[0]
                raw = item.get('liveStreamingDetails', {}).get('concurrentViewers')
                viewers = int(raw) if raw else None
                thumbs = item.get('snippet', {}).get('thumbnails', {})
                thumbnail_url = (
                    thumbs.get('maxres', {}).get('url')
                    or thumbs.get('standard', {}).get('url')
                    or thumbs.get('high', {}).get('url')
                )
                return viewers, thumbnail_url
            except Exception as exc:
                self.logger.warning(
                    'Failed to fetch video data for %s: %s', video_id, exc
                )
                return None, None

        async def get_channel_icon() -> str | None:
            try:
                with api_call_latency.labels(service='youtube').time():
                    async with self._session.get(
                        _YT_API_CHANNELS,
                        params={
                            'part': 'snippet',
                            'id': yt_channel_id,
                            'key': config.youtube_api_key,
                        },
                        timeout=_REQUEST_TIMEOUT,
                    ) as resp:
                        data = await resp.json()
                items = data.get('items', [])
                if not items:
                    return None
                thumbnails = items[0]['snippet']['thumbnails']
                return thumbnails.get('default', {}).get('url') or thumbnails.get(
                    'medium', {}
                ).get('url')
            except Exception as exc:
                self.logger.warning(
                    'Failed to fetch channel icon for %s: %s', yt_channel_id, exc
                )
                return None

        (viewers, thumbnail_url), icon_url = await asyncio.gather(
            get_video_data(), get_channel_icon()
        )
        return viewers, icon_url, thumbnail_url

    @staticmethod
    def _build_yt_live_embed(
        author_text: str,
        title: str,
        url: str,
        viewers: int | None,
        thumbnail_url: str | None,
        channel_icon_url: str | None,
    ) -> discord.Embed:
        """Build a Streamcord-style embed for a live YouTube stream.

        Args:
            author_text: Notification message shown next to the channel icon.
            title: Stream title, used as the clickable embed title.
            url: Stream URL.
            viewers: Live viewer count, or None if unavailable.
            thumbnail_url: Stream preview image URL from the API, or None.
            channel_icon_url: Channel profile picture URL, or None.

        Returns:
            A discord.Embed ready to post.
        """
        embed = discord.Embed(title=title, url=url, colour=discord.Colour(0xFF0000))
        if channel_icon_url:
            embed.set_author(name=author_text, icon_url=channel_icon_url)
        else:
            embed.set_author(name=author_text)
        if viewers is not None:
            embed.add_field(name='Viewers', value=f'{viewers:,}', inline=True)
        if thumbnail_url:
            embed.set_image(url=thumbnail_url)
        return embed

    # ------------------------------------------------------------------ polling

    async def _poll_all(self) -> None:
        """Poll every configured relay and forward new videos to Discord.

        Relays are grouped by YouTube channel so the RSS feed is fetched once
        per channel even when multiple Discord targets are configured.
        """
        relays = await get_all_relays()

        # Build a per-relay set of already-tracked live video IDs so resumed streams
        # (same video ID re-appearing in the feed) are not re-posted.
        live_sessions = await get_all_live_sessions()
        active_live_ids: dict[int, set[str]] = {}
        for s in live_sessions:
            active_live_ids.setdefault(s.relay_id, set()).add(s.video_id)

        grouped: dict[str, list[YouTubeRelay]] = {}
        for r in relays:
            grouped.setdefault(r.yt_channel_id, []).append(r)

        for yt_channel_id, channel_relays in grouped.items():
            try:
                await self._poll_youtube_channel(
                    yt_channel_id, channel_relays, active_live_ids
                )
            except Exception:
                self.logger.exception(
                    'Unexpected error polling YouTube channel %s', yt_channel_id
                )

        try:
            await self._check_live_sessions()
        except Exception:
            self.logger.exception('Unexpected error checking live sessions')

    async def _check_live_sessions(self) -> None:
        """Check all tracked live streams and post end-of-stream notices for ones that ended.

        Batches video IDs into requests of up to 50 (YouTube API limit). A session is
        removed when the stream ends, is gone (deleted/private), or cannot be found.
        API errors are treated conservatively — sessions are kept alive.
        """
        sessions = await get_all_live_sessions()
        if not sessions:
            return

        unique_ids = list({s.video_id for s in sessions})

        # 'live' → still streaming; 'ended' → actualEndTime set; 'gone' → not in API response.
        status: dict[str, str] = {vid: 'gone' for vid in unique_ids}

        for i in range(0, len(unique_ids), 50):
            batch = unique_ids[i : i + 50]
            try:
                with api_call_latency.labels(service='youtube').time():
                    async with self._session.get(
                        _YT_API_VIDEOS,
                        params={
                            'part': 'liveStreamingDetails',
                            'id': ','.join(batch),
                            'key': config.youtube_api_key,
                        },
                        timeout=_REQUEST_TIMEOUT,
                    ) as resp:
                        data = await resp.json()
            except Exception as exc:
                self.logger.warning('Failed to check live sessions batch: %s', exc)
                for vid in batch:
                    status[vid] = 'live'  # keep alive on error
                continue

            for item in data.get('items', []):
                vid = item['id']
                details = item.get('liveStreamingDetails', {})
                status[vid] = 'ended' if details.get('actualEndTime') else 'live'

        for s in sessions:
            vid_status = status.get(s.video_id, 'live')
            if vid_status == 'live':
                continue

            if vid_status == 'ended':
                relay = await get_relay_by_id(s.relay_id)
                if relay is not None:
                    channel = await resolve_channel(self.bot, relay.discord_channel_id)
                    if channel is not None:
                        end_embed = discord.Embed(
                            description=(
                                f'**{relay.yt_channel_title}** has finished streaming\n'
                                f'[Watch Recording](https://www.youtube.com/watch?v={s.video_id})\n'
                                f'[Visit Channel](https://www.youtube.com/channel/{relay.yt_channel_id})'
                            ),
                            colour=discord.Colour.greyple(),
                        )
                        end_embed.set_author(
                            name='Stream ended',
                            icon_url='https://www.gstatic.com/youtube/img/branding/favicon/favicon_96x96.png',
                        )
                        edited = False
                        if s.discord_message_id:
                            try:
                                original = await channel.fetch_message(
                                    s.discord_message_id
                                )
                                await original.edit(content=None, embed=end_embed)
                                edited = True
                            except (discord.NotFound, discord.Forbidden):
                                pass
                        if not edited:
                            try:
                                await channel.send(embed=end_embed)
                            except discord.Forbidden:
                                self.logger.warning(
                                    'No permission to post stream end in channel %d',
                                    relay.discord_channel_id,
                                )

            await remove_live_session(s.id)
            self.logger.info(
                'Live session removed for video %s (status: %s)', s.video_id, vid_status
            )

    async def _poll_youtube_channel(
        self,
        yt_channel_id: str,
        relays: list[YouTubeRelay],
        active_live_ids: dict[int, set[str]],
    ) -> None:
        """Fetch the RSS feed once and dispatch new entries to all relay rows.

        Args:
            yt_channel_id: YouTube channel ID to poll.
            relays: All relay rows for this YouTube channel.
            active_live_ids: Per-relay set of live video IDs already being tracked.
        """
        url = f'{_YT_RSS_BASE}?channel_id={yt_channel_id}'
        try:
            async with self._session.get(url, timeout=_REQUEST_TIMEOUT) as resp:
                content = await resp.text()
        except Exception as exc:
            self.logger.warning('Failed to fetch RSS for %s: %s', yt_channel_id, exc)
            relay_fetch_errors.labels(service='youtube').inc()
            return

        relay_last_poll.labels(service='youtube').set(time.time())
        feed = feedparser.parse(content)
        self.logger.info('Polling %s: %d entries', yt_channel_id, len(feed.entries))
        if not feed.entries:
            return

        # Cache classifications so each video is checked once across all relay rows.
        classification_cache: dict[str, tuple[bool, bool]] = {}
        for relay in relays:
            await self._poll_relay(
                relay, feed.entries, classification_cache, active_live_ids
            )

    async def _poll_relay(
        self,
        relay: YouTubeRelay,
        entries: list,
        classification_cache: dict[str, tuple[bool, bool]],
        active_live_ids: dict[int, set[str]],
    ) -> None:
        """Forward new entries from an already-fetched feed to one relay row.

        Args:
            relay: The relay row to process.
            entries: Parsed feed entries for this YouTube channel.
            classification_cache: Shared cache of (is_short, is_live) per video ID.
            active_live_ids: Per-relay set of live video IDs already being tracked.
        """
        seen_ids = set(relay.seen_video_ids or [])

        if relay.last_video_id is None:
            # Channel was empty when relay was added; sync silently without posting.
            latest_id = self._video_id_from_entry(entries[0])
            if latest_id:
                new_seen = [v for e in entries if (v := self._video_id_from_entry(e))]
                await update_last_video_id(relay.id, latest_id, new_seen[:_SEEN_WINDOW])
                self.logger.info(
                    'Initial sync for relay %d (%s): last_video_id=%s',
                    relay.id,
                    relay.yt_channel_id,
                    latest_id,
                )
            return

        new_entries = []
        for entry in entries:
            if self._video_id_from_entry(entry) == relay.last_video_id:
                break
            new_entries.append(entry)
        else:
            # Sentinel aged out of the RSS window; resync silently.
            latest_id = self._video_id_from_entry(entries[0])
            new_seen = [v for e in entries if (v := self._video_id_from_entry(e))]
            if latest_id:
                await update_last_video_id(relay.id, latest_id, new_seen[:_SEEN_WINDOW])
            self.logger.warning(
                'Relay %d: last_video_id %r aged out of feed; resynced to %s',
                relay.id,
                relay.last_video_id,
                latest_id,
            )
            return

        # Filter videos whose metadata was bumped and re-appeared before the sentinel.
        new_entries = [
            e for e in new_entries if self._video_id_from_entry(e) not in seen_ids
        ]

        if not new_entries:
            return

        channel = await resolve_channel(self.bot, relay.discord_channel_id)
        if channel is None:
            self.logger.warning(
                'Channel %d not found for relay %d',
                relay.discord_channel_id,
                relay.id,
            )
            return

        newly_posted_ids: list[str] = []
        posted = 0
        for entry in reversed(new_entries):
            link = entry.get('link')
            if not link:
                continue

            video_id = self._video_id_from_entry(entry)
            is_short, is_live = False, False
            if video_id:
                if video_id not in classification_cache:
                    classification_cache[video_id] = await self._classify_video(
                        video_id
                    )
                is_short, is_live = classification_cache[video_id]

            if is_live and not relay.post_lives:
                continue
            if is_short and not relay.post_shorts:
                continue
            if not is_live and not is_short and not relay.post_videos:
                continue

            # Skip live streams already tracked — same video ID re-appeared in feed (resume).
            if (
                is_live
                and video_id
                and video_id in active_live_ids.get(relay.id, set())
            ):
                continue

            try:
                if is_live and video_id:
                    (
                        viewers,
                        channel_icon_url,
                        thumbnail_url,
                    ) = await self._fetch_live_embed_data(video_id, relay.yt_channel_id)
                    author_text = (
                        relay.message_live
                        or f'{relay.yt_channel_title} is live on YouTube!'
                    )
                    embed = self._build_yt_live_embed(
                        author_text=author_text,
                        title=entry.get('title') or relay.yt_channel_title,
                        url=link,
                        viewers=viewers,
                        thumbnail_url=thumbnail_url,
                        channel_icon_url=channel_icon_url,
                    )
                    sent = await channel.send(embed=embed)
                    await add_live_session(relay.id, video_id, sent.id)
                    relay_posts.labels(service='youtube', type='live').inc()
                else:
                    message = self._notification_message(relay, is_short, is_live)
                    await channel.send(f'{message}\n{link}')
                    relay_posts.labels(
                        service='youtube', type='short' if is_short else 'video'
                    ).inc()
                posted += 1
                if video_id:
                    newly_posted_ids.append(video_id)
            except discord.Forbidden:
                self.logger.warning(
                    'No permission to post in channel %d for relay %d',
                    relay.discord_channel_id,
                    relay.id,
                )
                return

        latest_id = self._video_id_from_entry(entries[0])
        existing_seen = relay.seen_video_ids or []
        posted_set = set(newly_posted_ids)
        updated_seen = newly_posted_ids + [
            v for v in existing_seen if v not in posted_set
        ]
        if latest_id:
            await update_last_video_id(relay.id, latest_id, updated_seen[:_SEEN_WINDOW])
        self.logger.info('Relay %d: posted %d new video(s)', relay.id, posted)
