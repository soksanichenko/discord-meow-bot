"""Twitch EventSub webhook endpoint: verify, ack immediately, process in the background."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime

from aiohttp import web

from sources.lib.utils.logger import Logger
from sources.lib.utils.metrics import twitch_webhook_requests
from sources.lib.utils.twitch_eventsub import (
    MessageDeduplicator,
    Subscription,
    is_timestamp_fresh,
    verify_signature,
)

MAX_BODY_BYTES = 64 * 1024
_MESSAGE_TYPES = ('notification', 'webhook_callback_verification', 'revocation')


@dataclass(frozen=True)
class StreamEvent:
    """A stream.online or stream.offline event for one broadcaster."""

    user_id: str
    login: str
    display_name: str


class TwitchWebhookHandler:
    """Handle Twitch EventSub webhook requests.

    Every request is authenticated with the HMAC signature before its content is
    parsed. Notifications are acknowledged at once and processed as background
    tasks, because Twitch revokes subscriptions whose callback answers slowly.
    """

    def __init__(
        self,
        secret: str,
        on_online: Callable[[StreamEvent], Awaitable[None]],
        on_offline: Callable[[StreamEvent], Awaitable[None]],
        on_revocation: Callable[[Subscription], Awaitable[None]],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initialise the handler.

        Args:
            secret: The EventSub secret the subscriptions were created with.
            on_online: Called for every stream.online notification.
            on_offline: Called for every stream.offline notification.
            on_revocation: Called when Twitch revokes a subscription.
            now: Clock, injectable for tests.
        """
        self.logger = Logger()
        self._secret = secret
        self._on_online = on_online
        self._on_offline = on_offline
        self._on_revocation = on_revocation
        self._now = now
        self._dedup = MessageDeduplicator()
        self._tasks: set[asyncio.Task] = set()

    async def handle(self, request: web.Request) -> web.Response:
        """Process one webhook request.

        Args:
            request: The incoming aiohttp request.

        Returns:
            403 for a bad signature or a stale timestamp, 413 for an oversized
            body, 400 for malformed content, the raw challenge for a
            verification request and 204 for everything else.
        """
        message_type = request.headers.get('Twitch-Eventsub-Message-Type', '').lower()
        label = message_type if message_type in _MESSAGE_TYPES else 'unknown'

        def reply(result: str, response: web.Response) -> web.Response:
            twitch_webhook_requests.labels(type=label, result=result).inc()
            return response

        body = await self._read_body(request)
        if body is None:
            return reply('too_large', web.Response(status=413))

        message_id = request.headers.get('Twitch-Eventsub-Message-Id', '')
        timestamp = request.headers.get('Twitch-Eventsub-Message-Timestamp', '')
        signature = request.headers.get('Twitch-Eventsub-Message-Signature', '')
        if not verify_signature(self._secret, message_id, timestamp, body, signature):
            self.logger.warning(
                'Twitch webhook: signature mismatch, discarding message'
            )
            return reply('bad_signature', web.Response(status=403))
        if not is_timestamp_fresh(timestamp, self._now()):
            self.logger.warning('Twitch webhook: stale timestamp, discarding message')
            return reply('stale', web.Response(status=403))

        try:
            payload = json.loads(body)
            if message_type == 'webhook_callback_verification':
                return reply('ok', self._challenge(payload))
            if message_type == 'notification':
                return reply(*self._notification(payload, message_id))
            if message_type == 'revocation':
                return reply('ok', self._revocation(payload))
        except (ValueError, KeyError, TypeError, AttributeError):
            return reply('malformed', web.Response(status=400))
        return reply('malformed', web.Response(status=400))

    async def wait_for_pending(self) -> None:
        """Wait for in-flight background tasks (used by tests and shutdown)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    @staticmethod
    async def _read_body(request: web.Request) -> bytes | None:
        """Read the request body, or None if it exceeds MAX_BODY_BYTES."""
        if (
            request.content_length is not None
            and request.content_length > MAX_BODY_BYTES
        ):
            return None
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.content.iter_chunked(8192):
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                return None
            chunks.append(chunk)
        return b''.join(chunks)

    @staticmethod
    def _challenge(payload: dict) -> web.Response:
        """Answer a callback verification with the raw challenge."""
        challenge = payload['challenge']
        if not isinstance(challenge, str):
            raise TypeError('challenge must be a string')
        return web.Response(text=challenge, content_type='text/plain')

    def _notification(self, payload: dict, message_id: str) -> tuple[str, web.Response]:
        """Dispatch a notification and return (metric result, response)."""
        sub_type = payload['subscription']['type']
        callback = {
            'stream.online': self._on_online,
            'stream.offline': self._on_offline,
        }.get(sub_type)
        if callback is None:
            return 'ignored', web.Response(status=204)
        event = payload['event']
        stream_event = StreamEvent(
            user_id=event['broadcaster_user_id'],
            login=event['broadcaster_user_login'],
            display_name=event['broadcaster_user_name'],
        )
        if self._dedup.seen(message_id):
            return 'duplicate', web.Response(status=204)
        self._spawn(callback(stream_event), f'twitch-{sub_type}')
        return 'ok', web.Response(status=204)

    def _revocation(self, payload: dict) -> web.Response:
        """Dispatch a revocation notice."""
        sub = payload['subscription']
        revoked = Subscription(
            id=sub['id'],
            type=sub['type'],
            user_id=sub['condition']['broadcaster_user_id'],
            status=sub['status'],
        )
        self._spawn(self._on_revocation(revoked), 'twitch-revocation')
        return web.Response(status=204)

    def _spawn(self, coro: Coroutine, name: str) -> None:
        """Run a coroutine as a tracked background task."""
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        """Forget a finished task and log its unhandled exception, if any."""
        self._tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            self.logger.exception(
                'Unhandled error in %s', task.get_name(), exc_info=exc
            )


async def eventsub_route(request: web.Request) -> web.Response:
    """aiohttp view: hand the request to the Twitch relay cog's webhook handler.

    Args:
        request: The incoming request; ``request.app['bot']`` is the bot.

    Returns:
        The handler's response, or 404 if the cog is not loaded or the webhook
        is not configured (so a disabled host answers like an unknown path).
    """
    cog = request.app['bot'].get_cog('TwitchRelayCog')
    handler = getattr(cog, 'webhook_handler', None)
    if handler is None:
        return web.Response(status=404)
    return await handler.handle(request)
