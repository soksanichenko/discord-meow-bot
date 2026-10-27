"""Tests for the Twitch EventSub webhook handler and its aiohttp route."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from sources.lib.twitch_webhook import (
    MAX_BODY_BYTES,
    StreamEvent,
    TwitchWebhookHandler,
    eventsub_route,
)
from sources.lib.utils.metrics import twitch_webhook_requests
from sources.lib.utils.twitch_eventsub import Subscription

SECRET = 'a-secret-of-sufficient-length'
NOW = datetime(2026, 10, 27, 10, 0, 0, tzinfo=UTC)
FRESH = '2026-10-27T10:00:00.123456789Z'


def _signed(
    payload: dict | bytes,
    message_type: str = 'notification',
    message_id: str = 'm1',
    timestamp: str = FRESH,
    secret: str = SECRET,
) -> dict:
    """Build the kwargs for client.post(): a raw body plus Twitch's headers."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    message = message_id.encode() + timestamp.encode() + body
    signature = (
        'sha256=' + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    )
    return {
        'data': body,
        'headers': {
            'Twitch-Eventsub-Message-Id': message_id,
            'Twitch-Eventsub-Message-Timestamp': timestamp,
            'Twitch-Eventsub-Message-Signature': signature,
            'Twitch-Eventsub-Message-Type': message_type,
        },
    }


def _notification(sub_type: str = 'stream.online') -> dict:
    return {
        'subscription': {'id': 'sub-1', 'type': sub_type, 'status': 'enabled'},
        'event': {
            'broadcaster_user_id': '123',
            'broadcaster_user_login': 'streamer',
            'broadcaster_user_name': 'Streamer',
        },
    }


@pytest.fixture
async def env():
    on_online, on_offline, on_revocation = AsyncMock(), AsyncMock(), AsyncMock()
    handler = TwitchWebhookHandler(
        SECRET, on_online, on_offline, on_revocation, now=lambda: NOW
    )
    app = web.Application()
    app.router.add_post('/twitch/eventsub', handler.handle)
    async with TestClient(TestServer(app)) as client:
        yield SimpleNamespace(
            client=client,
            handler=handler,
            on_online=on_online,
            on_offline=on_offline,
            on_revocation=on_revocation,
        )


async def _post(env, **kwargs):
    return await env.client.post('/twitch/eventsub', **kwargs)


def _count(message_type: str, result: str) -> float:
    return twitch_webhook_requests.labels(type=message_type, result=result)._value.get()


class TestChallenge:
    async def test_answers_with_the_raw_challenge(self, env):
        resp = await _post(
            env,
            **_signed(
                {'challenge': 'abc'}, message_type='webhook_callback_verification'
            ),
        )

        assert resp.status == 200
        assert resp.content_type == 'text/plain'
        assert await resp.text() == 'abc'

    async def test_rejects_a_challenge_with_a_wrong_signature(self, env):
        kwargs = _signed(
            {'challenge': 'abc'}, message_type='webhook_callback_verification'
        )
        kwargs['headers']['Twitch-Eventsub-Message-Signature'] = 'sha256=00'

        resp = await _post(env, **kwargs)

        assert resp.status == 403


class TestNotification:
    async def test_stream_online_is_acked_then_processed(self, env):
        resp = await _post(env, **_signed(_notification('stream.online')))
        await env.handler.wait_for_pending()

        assert resp.status == 204
        env.on_online.assert_awaited_once_with(
            StreamEvent('123', 'streamer', 'Streamer')
        )
        env.on_offline.assert_not_awaited()

    async def test_stream_offline_goes_to_the_offline_callback(self, env):
        resp = await _post(env, **_signed(_notification('stream.offline')))
        await env.handler.wait_for_pending()

        assert resp.status == 204
        env.on_offline.assert_awaited_once_with(
            StreamEvent('123', 'streamer', 'Streamer')
        )
        env.on_online.assert_not_awaited()

    async def test_a_repeated_message_id_is_processed_once(self, env):
        first = await _post(env, **_signed(_notification(), message_id='dup'))
        second = await _post(env, **_signed(_notification(), message_id='dup'))
        await env.handler.wait_for_pending()

        assert (first.status, second.status) == (204, 204)
        env.on_online.assert_awaited_once()

    async def test_unknown_subscription_type_is_ignored(self, env):
        resp = await _post(env, **_signed(_notification('channel.follow')))
        await env.handler.wait_for_pending()

        assert resp.status == 204
        env.on_online.assert_not_awaited()
        env.on_offline.assert_not_awaited()

    async def test_a_failing_callback_does_not_change_the_response(self, env):
        env.on_online.side_effect = RuntimeError('boom')
        env.handler.logger = MagicMock()

        resp = await _post(env, **_signed(_notification()))
        await env.handler.wait_for_pending()

        assert resp.status == 204
        env.handler.logger.exception.assert_called_once()


class TestRevocation:
    async def test_revocation_reaches_its_callback(self, env):
        payload = {
            'subscription': {
                'id': 'sub-1',
                'type': 'stream.online',
                'status': 'authorization_revoked',
                'condition': {'broadcaster_user_id': '123'},
            }
        }

        resp = await _post(env, **_signed(payload, message_type='revocation'))
        await env.handler.wait_for_pending()

        assert resp.status == 204
        env.on_revocation.assert_awaited_once_with(
            Subscription(
                id='sub-1',
                type='stream.online',
                user_id='123',
                status='authorization_revoked',
            )
        )


class TestRejections:
    async def test_wrong_signature_is_rejected_without_processing(self, env):
        kwargs = _signed(_notification(), secret='another-secret-12')

        resp = await _post(env, **kwargs)
        await env.handler.wait_for_pending()

        assert resp.status == 403
        env.on_online.assert_not_awaited()

    async def test_a_correctly_signed_replay_is_rejected(self, env):
        stale = (NOW - timedelta(minutes=11)).strftime('%Y-%m-%dT%H:%M:%S.000000000Z')

        resp = await _post(env, **_signed(_notification(), timestamp=stale))
        await env.handler.wait_for_pending()

        assert resp.status == 403
        env.on_online.assert_not_awaited()

    async def test_malformed_json_with_a_valid_signature(self, env):
        resp = await _post(env, **_signed(b'{not json'))

        assert resp.status == 400

    async def test_unknown_message_type_with_a_valid_signature(self, env):
        resp = await _post(env, **_signed(_notification(), message_type='surprise'))

        assert resp.status == 400

    async def test_oversized_body_is_refused_without_running_callbacks(self, env):
        resp = await _post(env, **_signed(b'x' * (MAX_BODY_BYTES + 1)))
        await env.handler.wait_for_pending()

        assert resp.status == 413
        env.on_online.assert_not_awaited()

    async def test_oversized_chunked_body_is_refused(self, env):
        async def chunks():
            for _ in range(MAX_BODY_BYTES // 8192 + 2):
                yield b'x' * 8192

        kwargs = _signed(b'')
        kwargs['data'] = chunks()  # no Content-Length: sent with chunked encoding

        resp = await _post(env, **kwargs)

        assert resp.status == 413
        env.on_online.assert_not_awaited()


class TestMetrics:
    async def test_counts_accepted_and_rejected_requests(self, env):
        ok_before = _count('notification', 'ok')
        bad_before = _count('notification', 'bad_signature')

        await _post(env, **_signed(_notification()))
        await _post(env, **_signed(_notification(), secret='another-secret-12'))

        assert _count('notification', 'ok') == ok_before + 1
        assert _count('notification', 'bad_signature') == bad_before + 1


class TestRoute:
    @staticmethod
    def _request(cog) -> MagicMock:
        request = MagicMock()
        request.app = {'bot': MagicMock()}
        request.app['bot'].get_cog.return_value = cog
        return request

    async def test_404_when_the_cog_is_not_loaded(self):
        resp = await eventsub_route(self._request(None))

        assert resp.status == 404

    async def test_404_when_the_webhook_is_disabled(self):
        resp = await eventsub_route(
            self._request(SimpleNamespace(webhook_handler=None))
        )

        assert resp.status == 404

    async def test_delegates_to_the_handler(self):
        expected = web.Response(status=204)
        handler = SimpleNamespace(handle=AsyncMock(return_value=expected))
        request = self._request(SimpleNamespace(webhook_handler=handler))

        resp = await eventsub_route(request)

        assert resp is expected
        handler.handle.assert_awaited_once_with(request)
