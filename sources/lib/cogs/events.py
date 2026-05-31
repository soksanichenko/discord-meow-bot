"""Discord Scheduled Events auto-start cog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord.ext import commands

from sources.lib.utils.logger import Logger

_OVERDUE_DELAY_SECONDS = 5


async def _start_event(guild_id: int, event_id: int, bot: commands.Bot) -> None:
    """Fetch the scheduled event and transition it to ACTIVE status.

    Args:
        guild_id: The guild the event belongs to.
        event_id: The scheduled event ID.
        bot: The Discord bot instance.
    """
    logger = Logger()

    guild = bot.get_guild(guild_id)
    if guild is None:
        logger.warning('Guild %d not found when starting event %d', guild_id, event_id)
        return

    try:
        event = await guild.fetch_scheduled_event(event_id)
    except discord.NotFound:
        logger.debug(
            'Scheduled event %d in guild %d not found (deleted?)', event_id, guild_id
        )
        return
    except Exception as exc:
        logger.warning('Failed to fetch scheduled event %d: %s', event_id, exc)
        return

    if event.status != discord.EventStatus.scheduled:
        logger.debug(
            'Event %d status is %s, skipping auto-start', event_id, event.status
        )
        return

    try:
        await event.edit(status=discord.EventStatus.active)
        logger.info(
            'Auto-started event %d (%s) in guild %s', event_id, event.name, guild.name
        )
    except Exception as exc:
        logger.warning('Failed to auto-start event %d: %s', event_id, exc)


class EventsCog(commands.Cog):
    """Auto-starts Discord scheduled events when their start time arrives.

    Schedules a one-shot APScheduler job for each event in SCHEDULED status.
    Jobs are added/updated/removed in response to gateway events so the
    scheduler stays in sync without any polling.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog (does not start the scheduler yet).

        Args:
            bot: The Discord bot instance.
        """
        self._bot = bot
        self._scheduler = AsyncIOScheduler()
        self._logger = Logger()

    async def cog_load(self) -> None:
        """Start the scheduler and register jobs for all existing scheduled events."""
        self._scheduler.start()
        self._logger.info(
            'Events auto-start scheduler started (guild_scheduled_events intent: %s)',
            self._bot.intents.guild_scheduled_events,
        )

        for guild in self._bot.guilds:
            try:
                events = await guild.fetch_scheduled_events()
            except Exception as exc:
                self._logger.warning(
                    'Failed to fetch scheduled events for guild %s: %s', guild.name, exc
                )
                continue
            for event in events:
                if event.status == discord.EventStatus.scheduled:
                    self._schedule(event)

    async def cog_unload(self) -> None:
        """Stop the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._logger.info('Events auto-start scheduler stopped')

    def _schedule(self, event: discord.ScheduledEvent) -> None:
        """Add or replace the auto-start job for the given event.

        If the event's start time is already in the past (e.g. on bot restart),
        the job fires after a short delay so the notification is not lost.

        Args:
            event: The scheduled event to register.
        """
        run_date = event.start_time
        if run_date.tzinfo is None:
            run_date = run_date.replace(tzinfo=UTC)

        now = datetime.now(tz=UTC)
        if run_date <= now:
            run_date = now + timedelta(seconds=_OVERDUE_DELAY_SECONDS)

        self._scheduler.add_job(
            _start_event,
            trigger='date',
            run_date=run_date,
            args=[event.guild_id, event.id, self._bot],
            id=f'event_{event.guild_id}_{event.id}',
            replace_existing=True,
        )
        self._logger.info(
            'Scheduled auto-start for event %d (%s) at %s',
            event.id,
            event.name,
            run_date.isoformat(),
        )

    def _cancel(self, guild_id: int, event_id: int) -> None:
        """Remove the auto-start job for the given event if it exists.

        Args:
            guild_id: The guild the event belongs to.
            event_id: The scheduled event ID.
        """
        job = self._scheduler.get_job(f'event_{guild_id}_{event_id}')
        if job is not None:
            job.remove()
            self._logger.info('Cancelled auto-start job for event %d', event_id)

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event: discord.ScheduledEvent) -> None:
        """Schedule auto-start when a new event is created.

        Args:
            event: The newly created scheduled event.
        """
        if event.status == discord.EventStatus.scheduled:
            self._logger.info(
                'New scheduled event %d (%s) in guild %s',
                event.id,
                event.name,
                event.guild_id,
            )
            self._schedule(event)
        else:
            self._logger.debug(
                'Ignoring new event %d (%s) with status %s',
                event.id,
                event.name,
                event.status,
            )

    @commands.Cog.listener()
    async def on_scheduled_event_update(
        self, before: discord.ScheduledEvent, after: discord.ScheduledEvent
    ) -> None:
        """Reschedule or cancel auto-start when an event is modified.

        Handles start time changes, cancellations, and manual starts.

        Args:
            before: The event state before the update.
            after: The event state after the update.
        """
        self._logger.info(
            'Event %d (%s) updated: %s -> %s',
            after.id,
            after.name,
            before.status,
            after.status,
        )
        if after.status == discord.EventStatus.scheduled:
            self._schedule(after)
        else:
            self._cancel(after.guild_id, after.id)

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent) -> None:
        """Remove the auto-start job when an event is deleted.

        Args:
            event: The deleted scheduled event.
        """
        self._logger.info(
            'Event %d (%s) deleted in guild %s', event.id, event.name, event.guild_id
        )
        self._cancel(event.guild_id, event.id)
