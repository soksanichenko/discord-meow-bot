"""Birthdays cog — per-server birthday tracking, announcements, and role assignment."""

from __future__ import annotations

import asyncio
import calendar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import discord
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from discord.ext import commands

from sources.config import config
from sources.lib.db.models import GuildMemberBirthday, GuildSettings
from sources.lib.db.operations.birthdays import (
    get_all_unannounced_birthdays_for_guild,
    get_guild_birthdays,
    get_guild_member_birthday,
    get_guild_settings,
    mark_birthday_announced,
    remove_guild_member_birthday,
    set_guild_member_birthday,
    upsert_guild_settings,
)
from sources.lib.db.operations.users import get_user
from sources.lib.utils import Logger

_ANNOUNCEMENT_HOUR = 9
_MAX_IMAGE_SIZE = 2 * 1024 * 1024  # 2 MB
_ALLOWED_CONTENT_TYPES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}
_CONTENT_TYPE_TO_EXT = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif',
    'image/webp': 'webp',
}
_DEFAULT_MESSAGE = '🎂 Happy birthday, {mention}! 🎉'
_DEFAULT_MESSAGE_WITH_AGE = '🎂 Happy birthday, {mention}! You\'re turning **{age}** today! 🎉'




@dataclass
class _BirthdayEvent:
    """Carries all data needed to announce one birthday — no Discord API calls inside."""

    guild_id: int
    user_id: int
    channel_id: int
    role_id: int | None
    birth_year: int | None
    record: GuildMemberBirthday


class _SafeFormatDict(dict):
    """dict subclass that returns the key placeholder for missing keys."""

    def __missing__(self, key: str) -> str:
        return '{%s}' % key


def _format_message(
    template: str | None,
    member: discord.Member,
    birth_year: int | None,
    current_year: int,
) -> str:
    """Render the birthday announcement message.

    Uses the custom template if provided, otherwise falls back to the default.
    Available variables: {mention}, {username}, {display_name}, {age}.

    Args:
        template: Custom message template, or None for the default.
        member: The birthday member.
        birth_year: The member's birth year, or None if not set.
        current_year: The current calendar year.
    """
    age = _format_ordinal(current_year - birth_year) if birth_year else ''
    if template is None:
        template = _DEFAULT_MESSAGE_WITH_AGE if birth_year else _DEFAULT_MESSAGE
    return template.format_map(_SafeFormatDict(
        mention=member.mention,
        username=member.name,
        display_name=member.display_name,
        age=age,
    ))


def _format_ordinal(n: int) -> str:
    """Return n with its English ordinal suffix (1st, 2nd, 3rd, 4th…).

    Args:
        n: A positive integer.
    """
    if 11 <= (n % 100) <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return '%d%s' % (n, suffix)


def _validate_date(day: int, month: int, year: int | None) -> bool:
    """Return True if the day/month/year combination is a valid calendar date.

    Args:
        day: Day of month.
        month: Month number (1–12).
        year: Optional year; leap-year validation uses 2000 when omitted.
    """
    try:
        calendar.monthrange(year or 2000, month)
        return 1 <= day <= calendar.monthrange(year or 2000, month)[1]
    except (ValueError, OverflowError):
        return False


class BirthdaysCog(commands.Cog):
    """Per-server birthday tracking with daily announcements and birthday role assignment."""

    birthday = app_commands.Group(
        name='birthday',
        description='Birthday tracking commands',
    )

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog.

        Args:
            bot: The Discord bot instance.
        """
        self.bot = bot
        self._logger = Logger()
        self._scheduler = AsyncIOScheduler()

    async def cog_load(self) -> None:
        """Start the hourly birthday scheduler and ensure the images directory exists."""
        Path(config.birthday_images_dir).mkdir(parents=True, exist_ok=True)
        self._scheduler.add_job(
            self._birthday_cron_job,
            trigger='cron',
            minute=0,
            id='birthday_hourly',
            replace_existing=True,
        )
        self._scheduler.start()
        self._logger.info('Birthday scheduler started (runs every hour at :00)')

    def cog_unload(self) -> None:
        """Stop the birthday scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._logger.info('Birthday scheduler stopped')

    # ------------------------------------------------------------------
    # Cron job
    # ------------------------------------------------------------------

    async def _get_todays_birthday_events(self) -> list[_BirthdayEvent]:
        """Return unannounced birthday events where it is currently 9am in the user's timezone.

        Uses the user's personal timezone first; falls back to the guild timezone.
        Guilds without a birthday channel or any usable timezone are skipped.

        Pure data logic — no Discord delivery calls.
        """
        now_utc = datetime.now(tz=timezone.utc)
        events: list[_BirthdayEvent] = []

        for guild in self.bot.guilds:
            settings = await get_guild_settings(guild.id)
            if not settings or not settings.birthday_channel_id:
                continue

            birthdays = await get_all_unannounced_birthdays_for_guild(guild.id, now_utc.year)

            for bday in birthdays:
                if guild.get_member(bday.user_id) is None:
                    continue

                # Resolve effective timezone: user's own → guild fallback
                db_user = await get_user(bday.user_id)
                tz_str = (db_user.timezone if db_user else None) or settings.timezone
                if not tz_str:
                    continue

                try:
                    tz = pytz.timezone(tz_str)
                except pytz.UnknownTimeZoneError:
                    self._logger.warning('Unknown timezone %r for user %d', tz_str, bday.user_id)
                    continue

                local_now = now_utc.astimezone(tz)
                if local_now.hour != _ANNOUNCEMENT_HOUR:
                    continue
                if local_now.month != bday.birthday_month or local_now.day != bday.birthday_day:
                    continue

                events.append(_BirthdayEvent(
                    guild_id=guild.id,
                    user_id=bday.user_id,
                    channel_id=settings.birthday_channel_id,
                    role_id=settings.birthday_role_id,
                    birth_year=bday.birth_year,
                    record=bday,
                ))

        return events

    async def _send_birthday_greetings(self, events: list[_BirthdayEvent]) -> None:
        """Assign birthday roles and post announcements for each event.

        Args:
            events: List of birthday events produced by _get_todays_birthday_events.
        """
        today = datetime.now(tz=timezone.utc)

        for event in events:
            guild = self.bot.get_guild(event.guild_id)
            if guild is None:
                continue

            member = guild.get_member(event.user_id)
            if member is None:
                continue

            channel = guild.get_channel(event.channel_id)
            if channel is None:
                self._logger.warning(
                    'Birthday channel %d not found in guild %d', event.channel_id, event.guild_id,
                )
                continue

            # Assign birthday role
            if event.role_id:
                role = guild.get_role(event.role_id)
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role, reason='Birthday')
                    except discord.Forbidden:
                        self._logger.warning(
                            'Cannot assign birthday role in guild %d — missing permissions',
                            event.guild_id,
                        )
                    except discord.HTTPException as exc:
                        self._logger.warning('Failed to assign birthday role: %s', exc)

            settings = await get_guild_settings(event.guild_id)
            try:
                await self._send_announcement(channel, member, event.birth_year, settings, today.year)
            except (discord.Forbidden, discord.HTTPException) as exc:
                self._logger.warning(
                    'Failed to send birthday message in guild %d: %s', event.guild_id, exc,
                )
                continue

            await mark_birthday_announced(event.guild_id, event.user_id, today.year)
            self._logger.info(
                'Birthday announced for user %d in guild %d', event.user_id, event.guild_id,
            )

    @staticmethod
    async def _send_announcement(
        channel: discord.TextChannel,
        member: discord.Member,
        birth_year: int | None,
        settings: GuildSettings | None,
        current_year: int,
    ) -> None:
        """Build and send a birthday announcement embed to the given channel.

        Args:
            channel: The Discord text channel to post in.
            member: The birthday member.
            birth_year: The member's birth year, or None if not set.
            settings: Guild settings (may be None if not configured).
            current_year: The current calendar year.
        """
        template = settings.birthday_message if settings else None
        image_path_str = settings.birthday_image_path if settings else None

        message = _format_message(template, member, birth_year, current_year)
        embed = discord.Embed(description=message, colour=discord.Colour.gold())

        file: discord.File | None = None
        if image_path_str:
            image_path = Path(image_path_str)
            if image_path.exists():
                file = discord.File(image_path, filename='birthday%s' % image_path.suffix)
                embed.set_image(url='attachment://birthday%s' % image_path.suffix)

        if file:
            await channel.send(embed=embed, file=file)
        else:
            await channel.send(embed=embed)

    async def _remove_expired_birthday_roles(self) -> None:
        """Strip the birthday role from members whose birthday is no longer today."""
        today = datetime.now(tz=timezone.utc)

        for guild in self.bot.guilds:
            settings = await get_guild_settings(guild.id)
            if not settings or not settings.birthday_role_id:
                continue

            role = guild.get_role(settings.birthday_role_id)
            if role is None:
                continue

            for member in role.members:
                bday = await get_guild_member_birthday(guild.id, member.id)
                if bday is None or bday.birthday_month != today.month or bday.birthday_day != today.day:
                    try:
                        await member.remove_roles(role, reason='Birthday ended')
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        self._logger.warning(
                            'Failed to remove birthday role from %d in guild %d: %s',
                            member.id, guild.id, exc,
                        )

    async def _birthday_cron_job(self) -> None:
        """Daily cron job: remove expired roles, then send new birthday announcements."""
        self._logger.info('Running daily birthday job')
        try:
            await self._remove_expired_birthday_roles()
            events = await self._get_todays_birthday_events()
            await self._send_birthday_greetings(events)
            self._logger.info('Birthday job complete — announced %d birthday(s)', len(events))
        except Exception as exc:
            self._logger.error('Birthday cron job failed: %s', exc)

    # ------------------------------------------------------------------
    # User commands
    # ------------------------------------------------------------------

    @birthday.command(name='set', description='Set your birthday on this server')
    @app_commands.describe(
        day='Day of birth (1–31)',
        month='Month of birth (1–12)',
        year='Year of birth (optional — used to display your age)',
    )
    async def birthday_set(
        self,
        interaction: discord.Interaction,
        day: app_commands.Range[int, 1, 31],
        month: app_commands.Range[int, 1, 12],
        year: int | None = None,
    ) -> None:
        """Set your birthday on this server.

        Args:
            interaction: The Discord interaction.
            day: Day of birth.
            month: Month of birth.
            year: Optional birth year.
        """
        if not _validate_date(day, month, year):
            await interaction.response.send_message(
                'That date is not valid. Please check the day and month combination.',
                ephemeral=True,
            )
            return

        if year is not None and not (1900 <= year <= datetime.now(tz=timezone.utc).year):
            await interaction.response.send_message(
                'Year must be between 1900 and the current year.',
                ephemeral=True,
            )
            return

        await set_guild_member_birthday(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            day=day,
            month=month,
            year=year,
        )

        month_name = calendar.month_name[month]
        date_str = '%d %s' % (day, month_name)
        if year:
            date_str += ' %d' % year

        await interaction.response.send_message(
            'Your birthday has been set to **%s** on this server.' % date_str,
            ephemeral=True,
        )

    @birthday.command(name='remove', description='Remove your birthday from this server')
    async def birthday_remove(self, interaction: discord.Interaction) -> None:
        """Remove your birthday record from this server.

        Args:
            interaction: The Discord interaction.
        """
        deleted = await remove_guild_member_birthday(interaction.guild_id, interaction.user.id)
        if not deleted:
            await interaction.response.send_message(
                "You don't have a birthday set on this server.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            'Your birthday has been removed from this server.',
            ephemeral=True,
        )

    @birthday.command(name='view', description="View your or another member's birthday")
    @app_commands.describe(user='The member whose birthday you want to see (defaults to yourself)')
    async def birthday_view(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        """View a member's birthday on this server.

        Args:
            interaction: The Discord interaction.
            user: The member to look up; defaults to the caller.
        """
        target = user or interaction.user
        bday = await get_guild_member_birthday(interaction.guild_id, target.id)

        if bday is None:
            if target == interaction.user:
                msg = "You don't have a birthday set on this server."
            else:
                msg = '**%s** has not set a birthday on this server.' % target.display_name
            await interaction.response.send_message(msg, ephemeral=True)
            return

        month_name = calendar.month_name[bday.birthday_month]
        date_str = '%d %s' % (bday.birthday_day, month_name)
        if bday.birth_year:
            date_str += ' %d' % bday.birth_year

        if target == interaction.user:
            msg = 'Your birthday on this server is set to **%s**.' % date_str
        else:
            msg = "**%s**'s birthday on this server is **%s**." % (target.display_name, date_str)

        await interaction.response.send_message(msg, ephemeral=True)

    @birthday.command(name='list', description='View all birthdays set on this server')
    async def birthday_list(self, interaction: discord.Interaction) -> None:
        """List all birthday records for this server.

        Args:
            interaction: The Discord interaction.
        """
        await interaction.response.defer(ephemeral=True)
        birthdays = await get_guild_birthdays(interaction.guild_id)

        if not birthdays:
            await interaction.followup.send('No birthdays have been set on this server.')
            return

        embed = discord.Embed(
            title='Birthdays on this server',
            colour=discord.Colour.blurple(),
        )

        lines: list[str] = []
        for bday in birthdays:
            member = interaction.guild.get_member(bday.user_id)
            name = member.display_name if member else 'Unknown user (%d)' % bday.user_id
            month_name = calendar.month_abbr[bday.birthday_month]
            date_str = '%d %s' % (bday.birthday_day, month_name)
            if bday.birth_year:
                date_str += ' %d' % bday.birth_year
            lines.append('**%s** — %s' % (name, date_str))

        # Discord embed field value limit is 1024 chars; split into chunks if needed.
        chunk: list[str] = []
        chunk_len = 0
        field_index = 0
        for line in lines:
            if chunk_len + len(line) + 1 > 1024:
                embed.add_field(
                    name='\u200b' if field_index > 0 else 'Members',
                    value='\n'.join(chunk),
                    inline=False,
                )
                chunk = []
                chunk_len = 0
                field_index += 1
            chunk.append(line)
            chunk_len += len(line) + 1

        if chunk:
            embed.add_field(
                name='\u200b' if field_index > 0 else 'Members',
                value='\n'.join(chunk),
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------

    @birthday.command(name='channel-set', description='Set the birthday announcement channel')
    @app_commands.describe(channel='The text channel where birthday announcements will be posted')
    @app_commands.default_permissions(manage_guild=True)
    async def channel_set(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        """Configure the birthday announcement channel for this server.

        Args:
            interaction: The Discord interaction.
            channel: The target text channel.
        """
        await upsert_guild_settings(interaction.guild_id, birthday_channel_id=channel.id)
        await interaction.response.send_message(
            'Birthday announcements will be posted in %s.' % channel.mention,
            ephemeral=True,
        )

    @birthday.command(name='channel-remove', description='Remove the configured birthday announcement channel')
    @app_commands.default_permissions(manage_guild=True)
    async def channel_remove(self, interaction: discord.Interaction) -> None:
        """Clear the birthday announcement channel for this server.

        Args:
            interaction: The Discord interaction.
        """
        await upsert_guild_settings(interaction.guild_id, birthday_channel_id=None)
        await interaction.response.send_message(
            'Birthday announcement channel has been removed.',
            ephemeral=True,
        )

    @birthday.command(name='role-set', description='Set the birthday role')
    @app_commands.describe(role='The role to assign to members on their birthday')
    @app_commands.default_permissions(manage_guild=True)
    async def role_set(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        """Configure the birthday role for this server.

        Args:
            interaction: The Discord interaction.
            role: The role to assign on birthdays.
        """
        if role.managed or role.is_default():
            await interaction.response.send_message(
                '**%s** is managed by an integration and cannot be used as a birthday role.' % role.name,
                ephemeral=True,
            )
            return

        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                "**%s** is at or above the bot's highest role and cannot be assigned." % role.name,
                ephemeral=True,
            )
            return

        await upsert_guild_settings(interaction.guild_id, birthday_role_id=role.id)
        await interaction.response.send_message(
            '**%s** will be assigned to members on their birthday.' % role.name,
            ephemeral=True,
        )

    @birthday.command(name='role-remove', description='Remove the configured birthday role')
    @app_commands.default_permissions(manage_guild=True)
    async def role_remove(self, interaction: discord.Interaction) -> None:
        """Clear the birthday role for this server.

        Args:
            interaction: The Discord interaction.
        """
        await upsert_guild_settings(interaction.guild_id, birthday_role_id=None)
        await interaction.response.send_message(
            'Birthday role has been removed.',
            ephemeral=True,
        )

    @birthday.command(name='message-set', description='Set a custom birthday announcement message')
    @app_commands.describe(message=(
        'Message text. Variables: {mention}, {username}, {display_name}, {age}'
    ))
    @app_commands.default_permissions(manage_guild=True)
    async def message_set(self, interaction: discord.Interaction, message: str) -> None:
        """Set a custom birthday announcement message template for this server.

        Args:
            interaction: The Discord interaction.
            message: The message template string.
        """
        await upsert_guild_settings(interaction.guild_id, birthday_message=message)
        await interaction.response.send_message(
            'Birthday announcement message has been set.',
            ephemeral=True,
        )

    @birthday.command(name='message-remove', description='Remove the custom birthday announcement message')
    @app_commands.default_permissions(manage_guild=True)
    async def message_remove(self, interaction: discord.Interaction) -> None:
        """Clear the custom birthday message, reverting to the default.

        Args:
            interaction: The Discord interaction.
        """
        await upsert_guild_settings(interaction.guild_id, birthday_message=None)
        await interaction.response.send_message(
            'Custom birthday message removed. The default message will be used.',
            ephemeral=True,
        )

    @birthday.command(name='image-set', description='Set a custom image for birthday announcements')
    @app_commands.describe(image='Image file (PNG, JPG, GIF, WebP — max 2 MB)')
    @app_commands.default_permissions(manage_guild=True)
    async def image_set(self, interaction: discord.Interaction, image: discord.Attachment) -> None:
        """Upload and save a custom birthday announcement image for this server.

        Args:
            interaction: The Discord interaction.
            image: The uploaded image attachment.
        """
        await interaction.response.defer(ephemeral=True)

        if image.content_type not in _ALLOWED_CONTENT_TYPES:
            await interaction.followup.send(
                'Only PNG, JPG, GIF, and WebP images are supported.',
                ephemeral=True,
            )
            return

        if image.size > _MAX_IMAGE_SIZE:
            await interaction.followup.send(
                'Image must be 2 MB or smaller (received %.1f MB).' % (image.size / 1024 / 1024),
                ephemeral=True,
            )
            return

        data = await image.read()
        ext = _CONTENT_TYPE_TO_EXT[image.content_type]

        images_dir = Path(config.birthday_images_dir)
        for old_file in images_dir.glob('guild_%d_birthday.*' % interaction.guild_id):
            await asyncio.to_thread(old_file.unlink)

        image_path = images_dir / ('guild_%d_birthday.%s' % (interaction.guild_id, ext))
        await asyncio.to_thread(image_path.write_bytes, data)

        await upsert_guild_settings(interaction.guild_id, birthday_image_path=str(image_path))
        await interaction.followup.send(
            'Birthday announcement image has been set.',
            ephemeral=True,
        )

    @birthday.command(name='image-remove', description='Remove the custom birthday announcement image')
    @app_commands.default_permissions(manage_guild=True)
    async def image_remove(self, interaction: discord.Interaction) -> None:
        """Delete the stored birthday image and clear the setting for this server.

        Args:
            interaction: The Discord interaction.
        """
        settings = await get_guild_settings(interaction.guild_id)
        if settings and settings.birthday_image_path:
            path = Path(settings.birthday_image_path)
            if path.exists():
                await asyncio.to_thread(path.unlink)

        await upsert_guild_settings(interaction.guild_id, birthday_image_path=None)
        await interaction.response.send_message(
            'Birthday announcement image has been removed.',
            ephemeral=True,
        )

    @birthday.command(name='preview', description='Preview the birthday announcement')
    @app_commands.describe(user='Member to use as the birthday person (defaults to yourself)')
    @app_commands.default_permissions(manage_guild=True)
    async def preview(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        """Send a test birthday announcement to the current channel.

        Args:
            interaction: The Discord interaction.
            user: The member to use as the birthday person; defaults to the caller.
        """
        await interaction.response.defer()

        target = user or interaction.user
        settings = await get_guild_settings(interaction.guild_id)
        bday = await get_guild_member_birthday(interaction.guild_id, target.id)
        birth_year = bday.birth_year if bday else None
        today = datetime.now(tz=timezone.utc)

        template = settings.birthday_message if settings else None
        image_path_str = settings.birthday_image_path if settings else None

        message = _format_message(template, target, birth_year, today.year)
        embed = discord.Embed(description=message, colour=discord.Colour.gold())
        embed.set_footer(text='Preview — this is not a real announcement')

        file: discord.File | None = None
        if image_path_str:
            image_path = Path(image_path_str)
            if image_path.exists():
                file = discord.File(image_path, filename='birthday%s' % image_path.suffix)
                embed.set_image(url='attachment://birthday%s' % image_path.suffix)

        if file:
            await interaction.followup.send(embed=embed, file=file)
        else:
            await interaction.followup.send(embed=embed)

    @birthday.command(name='force', description="Set a birthday for another member (admin)")
    @app_commands.describe(
        user='The member whose birthday to set',
        day='Day of birth (1–31)',
        month='Month of birth (1–12)',
        year='Year of birth (optional)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def force_birthday(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        day: app_commands.Range[int, 1, 31],
        month: app_commands.Range[int, 1, 12],
        year: int | None = None,
    ) -> None:
        """Force-set another member's birthday on this server.

        Args:
            interaction: The Discord interaction.
            user: The target member.
            day: Day of birth.
            month: Month of birth.
            year: Optional birth year.
        """
        if not _validate_date(day, month, year):
            await interaction.response.send_message(
                'That date is not valid.',
                ephemeral=True,
            )
            return

        if year is not None and not (1900 <= year <= datetime.now(tz=timezone.utc).year):
            await interaction.response.send_message(
                'Year must be between 1900 and the current year.',
                ephemeral=True,
            )
            return

        await set_guild_member_birthday(
            guild_id=interaction.guild_id,
            user_id=user.id,
            day=day,
            month=month,
            year=year,
        )

        month_name = calendar.month_name[month]
        date_str = '%d %s' % (day, month_name)
        if year:
            date_str += ' %d' % year

        await interaction.response.send_message(
            "Birthday for **%s** has been set to **%s**." % (user.display_name, date_str),
            ephemeral=True,
        )

    @birthday.command(name='purge', description="Remove a member's birthday from this server (admin)")
    @app_commands.describe(user='The member whose birthday to remove')
    @app_commands.default_permissions(manage_guild=True)
    async def purge_birthday(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """Remove another member's birthday record from this server.

        Args:
            interaction: The Discord interaction.
            user: The target member.
        """
        deleted = await remove_guild_member_birthday(interaction.guild_id, user.id)
        if not deleted:
            await interaction.response.send_message(
                '**%s** does not have a birthday set on this server.' % user.display_name,
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Birthday for **%s** has been removed." % user.display_name,
            ephemeral=True,
        )
