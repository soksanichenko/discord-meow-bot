"""Tests for the Twitch EventSub webhook subscription manager."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sources.lib.twitch_subscriptions import TwitchSubscriptionManager
from sources.lib.utils.metrics import (
    twitch_subscription_cost,
    twitch_subscriptions_desired,
    twitch_subscriptions_enabled,
)

URL = 'https://example.com/discord-bot/twitch/eventsub'
SECRET = 'a-secret-of-sufficient-length'
TRANSPORT = {'method': 'webhook', 'callback': URL, 'secret': SECRET}


def _raw(
    sub_id: str,
    sub_type: str,
    user_id: str,
    status: str = 'enabled',
    transport: dict | None = None,
) -> SimpleNamespace:
    """A subscription object shaped like twitchAPI's EventSubSubscription."""
    return SimpleNamespace(
        id=sub_id,
        type=sub_type,
        status=status,
        condition={'broadcaster_user_id': user_id},
        transport=transport or {'method': 'webhook', 'callback': URL},
    )


class _Result:
    """Async-iterable stand-in for GetEventSubSubscriptionResult."""

    def __init__(self, subs, total_cost: int = 0, max_total_cost: int = 10000) -> None:
        self._subs = list(subs)
        self.total_cost = total_cost
        self.max_total_cost = max_total_cost

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._subs:
            raise StopAsyncIteration
        return self._subs.pop(0)


def _manager(subs=(), **result_kwargs) -> tuple[TwitchSubscriptionManager, MagicMock]:
    twitch = MagicMock()
    twitch.get_eventsub_subscriptions = AsyncMock(
        side_effect=lambda: _Result(list(subs), **result_kwargs)
    )
    twitch.create_eventsub_subscription = AsyncMock()
    twitch.delete_eventsub_subscription = AsyncMock()
    return TwitchSubscriptionManager(twitch, URL, SECRET), twitch


def _created(twitch: MagicMock) -> list[tuple[str, str]]:
    return sorted(
        (c.args[0], c.args[2]['broadcaster_user_id'])
        for c in twitch.create_eventsub_subscription.await_args_list
    )


class TestListSubscriptions:
    async def test_keeps_only_our_webhook_subscriptions(self):
        manager, _ = _manager(
            [
                _raw('a', 'stream.online', '1'),
                _raw(
                    'b',
                    'stream.online',
                    '2',
                    transport={'method': 'webhook', 'callback': 'https://other/cb'},
                ),
                _raw(
                    'c',
                    'stream.online',
                    '3',
                    transport={'method': 'websocket', 'session_id': 's'},
                ),
                _raw('d', 'channel.follow', '4'),
            ]
        )

        subs = await manager.list_subscriptions()

        assert [(s.id, s.type, s.user_id, s.status) for s in subs] == [
            ('a', 'stream.online', '1', 'enabled')
        ]

    async def test_publishes_the_subscription_cost_gauges(self):
        manager, _ = _manager([], total_cost=7, max_total_cost=9000)

        await manager.list_subscriptions()

        assert twitch_subscription_cost.labels(kind='total')._value.get() == 7
        assert twitch_subscription_cost.labels(kind='max')._value.get() == 9000


class TestReconcile:
    async def test_creates_both_types_for_a_missing_channel(self):
        manager, twitch = _manager([])

        failed = await manager.reconcile({'123'})

        assert failed == set()
        assert _created(twitch) == [('stream.offline', '123'), ('stream.online', '123')]
        twitch.create_eventsub_subscription.assert_any_await(
            'stream.online', '1', {'broadcaster_user_id': '123'}, TRANSPORT
        )

    async def test_deletes_subscriptions_of_channels_without_a_relay(self):
        manager, twitch = _manager(
            [_raw('s1', 'stream.online', '9'), _raw('s2', 'stream.offline', '9')]
        )

        failed = await manager.reconcile(set())

        assert failed == set()
        deleted = sorted(
            c.args[0] for c in twitch.delete_eventsub_subscription.await_args_list
        )
        assert deleted == ['s1', 's2']
        twitch.create_eventsub_subscription.assert_not_awaited()

    async def test_a_failed_create_is_reported_and_others_still_run(self):
        manager, twitch = _manager([])

        async def create(sub_type, version, condition, transport):
            if condition['broadcaster_user_id'] == '1':
                raise RuntimeError('callback unreachable')

        twitch.create_eventsub_subscription.side_effect = create

        failed = await manager.reconcile({'1', '2'})

        assert failed == {'1'}
        assert ('stream.online', '2') in _created(twitch)
        assert ('stream.offline', '2') in _created(twitch)

    async def test_a_failed_delete_is_reported_for_its_channel(self):
        manager, twitch = _manager([_raw('s1', 'stream.online', '9')])
        twitch.delete_eventsub_subscription.side_effect = RuntimeError('boom')

        assert await manager.reconcile(set()) == {'9'}

    async def test_user_removed_subscriptions_are_left_alone(self):
        manager, twitch = _manager(
            [
                _raw('s1', 'stream.online', '1', 'user_removed'),
                _raw('s2', 'stream.offline', '1', 'user_removed'),
            ]
        )

        failed = await manager.reconcile({'1'})

        assert failed == set()
        twitch.create_eventsub_subscription.assert_not_awaited()
        twitch.delete_eventsub_subscription.assert_not_awaited()

    async def test_a_replaced_subscription_is_deleted_before_it_is_recreated(self):
        manager, twitch = _manager(
            [
                _raw('s1', 'stream.online', '1', 'notification_failures_exceeded'),
                _raw('s2', 'stream.offline', '1'),
            ]
        )
        calls: list[str] = []

        async def slow_delete(*_args):
            await asyncio.sleep(0.02)
            calls.append('deleted')

        async def create(*_args):
            calls.append('created')

        twitch.delete_eventsub_subscription.side_effect = slow_delete
        twitch.create_eventsub_subscription.side_effect = create

        await manager.reconcile({'1'})

        assert calls == ['deleted', 'created']

    async def test_publishes_desired_and_enabled_gauges(self):
        manager, _ = _manager(
            [_raw('s1', 'stream.online', '1'), _raw('s2', 'stream.offline', '1')]
        )

        await manager.reconcile({'1'})

        assert twitch_subscriptions_desired._value.get() == 2
        assert twitch_subscriptions_enabled._value.get() == 2

    async def test_never_exceeds_the_concurrency_limit(self):
        manager, twitch = _manager([])
        running = peak = 0

        async def create(*_args):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1

        twitch.create_eventsub_subscription.side_effect = create

        await manager.reconcile({str(i) for i in range(20)})

        assert 1 < peak <= 5


class TestRecreate:
    async def test_deletes_ours_then_creates_both_types(self):
        manager, twitch = _manager(
            [
                _raw('s1', 'stream.online', '123'),
                _raw('s2', 'stream.offline', '123'),
                _raw('s3', 'stream.online', '999'),
            ]
        )

        failed = await manager.recreate({'123'})

        assert failed == set()
        deleted = sorted(
            c.args[0] for c in twitch.delete_eventsub_subscription.await_args_list
        )
        assert deleted == ['s1', 's2']
        assert _created(twitch) == [('stream.offline', '123'), ('stream.online', '123')]

    async def test_reports_channels_that_failed_to_recreate(self):
        manager, twitch = _manager([])
        twitch.create_eventsub_subscription.side_effect = RuntimeError('boom')

        assert await manager.recreate({'123'}) == {'123'}
