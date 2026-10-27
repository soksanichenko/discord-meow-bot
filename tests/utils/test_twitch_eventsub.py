"""Tests for the pure Twitch EventSub helpers."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import pytest

from sources.lib.utils.twitch_eventsub import (
    MessageDeduplicator,
    Subscription,
    SubscriptionPlan,
    format_missed_start_note,
    is_timestamp_fresh,
    plan_subscriptions,
    verify_signature,
)

SECRET = 'a-secret-of-sufficient-length'
MESSAGE_ID = 'befa7b53-d79d-478f-86b9-120f112b044e'
TIMESTAMP = '2026-10-27T10:00:00.634234626Z'
BODY = b'{"subscription": {"id": "x"}}'


def _signature(secret: str = SECRET, body: bytes = BODY) -> str:
    message = MESSAGE_ID.encode() + TIMESTAMP.encode() + body
    return 'sha256=' + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_accepts_the_documented_scheme(self):
        assert verify_signature(SECRET, MESSAGE_ID, TIMESTAMP, BODY, _signature())

    def test_rejects_a_tampered_body(self):
        assert not verify_signature(
            SECRET, MESSAGE_ID, TIMESTAMP, BODY + b' ', _signature()
        )

    def test_rejects_a_wrong_secret(self):
        assert not verify_signature(
            SECRET, MESSAGE_ID, TIMESTAMP, BODY, _signature(secret='another-secret-12')
        )

    def test_rejects_a_signature_without_the_prefix(self):
        bare = _signature().removeprefix('sha256=')

        assert not verify_signature(SECRET, MESSAGE_ID, TIMESTAMP, BODY, bare)

    def test_rejects_an_empty_signature(self):
        assert not verify_signature(SECRET, MESSAGE_ID, TIMESTAMP, BODY, '')


class TestIsTimestampFresh:
    NOW = datetime(2026, 10, 27, 10, 0, 0, tzinfo=UTC)

    def test_accepts_nanosecond_timestamp_within_the_window(self):
        assert is_timestamp_fresh(TIMESTAMP, self.NOW + timedelta(minutes=9))

    def test_rejects_a_stale_timestamp(self):
        assert not is_timestamp_fresh(TIMESTAMP, self.NOW + timedelta(minutes=11))

    def test_rejects_garbage(self):
        assert not is_timestamp_fresh('not-a-timestamp', self.NOW)

    def test_rejects_a_timestamp_too_far_in_the_future(self):
        assert not is_timestamp_fresh(TIMESTAMP, self.NOW - timedelta(minutes=11))


class TestMessageDeduplicator:
    def test_second_sighting_of_an_id_is_reported(self):
        dedup = MessageDeduplicator()

        assert dedup.seen('a') is False
        assert dedup.seen('a') is True

    def test_oldest_ids_are_evicted_beyond_max_size(self):
        dedup = MessageDeduplicator(max_size=2)
        for message_id in ('a', 'b', 'c'):
            dedup.seen(message_id)

        assert dedup.seen('a') is False


def _sub(
    sub_id: str, sub_type: str, user_id: str, status: str = 'enabled'
) -> Subscription:
    return Subscription(id=sub_id, type=sub_type, user_id=user_id, status=status)


ONLINE, OFFLINE = 'stream.online', 'stream.offline'


class TestPlanSubscriptions:
    def test_creates_both_types_when_nothing_exists(self):
        plan = plan_subscriptions({'1'}, [])

        assert plan == SubscriptionPlan(
            create=[(ONLINE, '1'), (OFFLINE, '1')], delete=[]
        )

    def test_empty_plan_when_everything_is_enabled(self):
        actual = [_sub('a', ONLINE, '1'), _sub('b', OFFLINE, '1')]

        assert plan_subscriptions({'1'}, actual) == SubscriptionPlan([], [])

    def test_deletes_subscriptions_of_channels_without_a_relay(self):
        actual = [_sub('a', ONLINE, '9'), _sub('b', OFFLINE, '9')]

        plan = plan_subscriptions(set(), actual)

        assert sorted(plan.delete) == ['a', 'b']
        assert plan.create == []

    def test_recreates_a_subscription_revoked_for_failures(self):
        actual = [
            _sub('a', ONLINE, '1', 'notification_failures_exceeded'),
            _sub('b', OFFLINE, '1'),
        ]

        plan = plan_subscriptions({'1'}, actual)

        assert plan.delete == ['a']
        assert plan.create == [(ONLINE, '1')]

    def test_keeps_a_subscription_pending_verification(self):
        actual = [
            _sub('a', ONLINE, '1', 'webhook_callback_verification_pending'),
            _sub('b', OFFLINE, '1'),
        ]

        assert plan_subscriptions({'1'}, actual) == SubscriptionPlan([], [])

    def test_leaves_a_user_removed_subscription_alone(self):
        actual = [
            _sub('a', ONLINE, '1', 'user_removed'),
            _sub('b', OFFLINE, '1', 'user_removed'),
        ]

        assert plan_subscriptions({'1'}, actual) == SubscriptionPlan([], [])

    def test_deletes_the_extra_duplicate_with_the_greater_id(self):
        actual = [
            _sub('b', ONLINE, '1'),
            _sub('a', ONLINE, '1'),
            _sub('c', OFFLINE, '1'),
        ]

        plan = plan_subscriptions({'1'}, actual)

        assert plan.delete == ['b']
        assert plan.create == []


class TestFormatMissedStartNote:
    NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        ('elapsed', 'expected'),
        [
            (timedelta(minutes=83, seconds=30), 'Already live for 1h 23m'),
            (timedelta(minutes=5), 'Already live for 5m'),
            (timedelta(seconds=20), 'Already live for under a minute'),
            (timedelta(hours=25, minutes=3), 'Already live for 25h 3m'),
        ],
    )
    def test_formats_elapsed_time(self, elapsed, expected):
        note = format_missed_start_note(self.NOW - elapsed, self.NOW)

        assert note == f'{expected} — the start of the stream was missed'
