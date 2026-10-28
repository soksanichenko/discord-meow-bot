"""Pure helpers for Twitch EventSub webhooks: signatures, dedupe, subscription plans."""

import hashlib
import hmac
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

SUBSCRIPTION_TYPES: tuple[str, ...] = ('stream.online', 'stream.offline')
# Twitch's replay-attack guidance: ignore messages whose timestamp is older than
# 10 minutes (https://dev.twitch.tv/docs/eventsub/#guarding-against-replay-attacks).
MAX_MESSAGE_AGE = timedelta(minutes=10)

_HEALTHY_STATUSES = frozenset({'enabled', 'webhook_callback_verification_pending'})
# The Twitch account is gone; recreating the subscription can never succeed.
_USER_REMOVED = 'user_removed'


@dataclass(frozen=True)
class Subscription:
    """A webhook subscription as listed by Helix."""

    id: str
    type: str
    user_id: str
    status: str


@dataclass(frozen=True)
class SubscriptionPlan:
    """What a reconcile has to do to reach the desired subscription set."""

    create: list[tuple[str, str]]
    delete: list[str]


def verify_signature(
    secret: str, message_id: str, timestamp: str, body: bytes, signature: str
) -> bool:
    """Check Twitch's HMAC-SHA256 signature of a webhook message.

    Args:
        secret: The EventSub secret the subscriptions were created with.
        message_id: Value of the Twitch-Eventsub-Message-Id header.
        timestamp: Value of the Twitch-Eventsub-Message-Timestamp header.
        body: The raw request body.
        signature: Value of the Twitch-Eventsub-Message-Signature header.

    Returns:
        True if the signature matches, compared in constant time.
    """
    message = message_id.encode() + timestamp.encode() + body
    expected = (
        'sha256=' + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected.encode(), signature.encode())


def is_timestamp_fresh(
    timestamp: str, now: datetime, max_age: timedelta = MAX_MESSAGE_AGE
) -> bool:
    """Check a message timestamp is within ``max_age`` of ``now`` (either side).

    Args:
        timestamp: RFC3339 timestamp from the Twitch-Eventsub-Message-Timestamp
            header (Twitch sends nanoseconds, which Python truncates).
        now: The current time, timezone-aware.
        max_age: Maximum accepted age.

    Returns:
        True if the timestamp parses and is fresh; False otherwise.
    """
    try:
        sent_at = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    if sent_at.tzinfo is None:
        return False
    return abs(now - sent_at) <= max_age


class MessageDeduplicator:
    """Remember the most recent webhook message ids to drop redeliveries."""

    def __init__(self, max_size: int = 1000) -> None:
        """Initialise the deduplicator.

        Args:
            max_size: How many message ids to remember before evicting the oldest.
        """
        self._max_size = max_size
        self._ids: OrderedDict[str, None] = OrderedDict()

    def seen(self, message_id: str) -> bool:
        """Record a message id.

        Args:
            message_id: Value of the Twitch-Eventsub-Message-Id header.

        Returns:
            True if the id was already recorded, False the first time.
        """
        if message_id in self._ids:
            return True
        self._ids[message_id] = None
        if len(self._ids) > self._max_size:
            self._ids.popitem(last=False)
        return False


def plan_subscriptions(
    desired_user_ids: set[str], actual: list[Subscription]
) -> SubscriptionPlan:
    """Work out which subscriptions to create and delete.

    Every desired channel needs one healthy subscription per type. Unhealthy
    ones (revoked, failed verification) are replaced, duplicates and those of
    channels without a relay are deleted, and ``user_removed`` ones are left
    alone because they can never be recreated.

    Args:
        desired_user_ids: Twitch user ids that have at least one relay.
        actual: Our webhook subscriptions as listed by Helix.

    Returns:
        The subscriptions to create as (type, user_id) and the ids to delete.
    """
    by_key: dict[tuple[str, str], list[Subscription]] = defaultdict(list)
    for sub in sorted(actual, key=lambda s: s.id):
        if sub.type in SUBSCRIPTION_TYPES:
            by_key[(sub.type, sub.user_id)].append(sub)

    create: list[tuple[str, str]] = []
    delete: list[str] = []

    for (_, user_id), subs in by_key.items():
        if user_id not in desired_user_ids:
            delete.extend(s.id for s in subs)

    for user_id in sorted(desired_user_ids):
        for sub_type in SUBSCRIPTION_TYPES:
            subs = by_key.get((sub_type, user_id), [])
            healthy = [s for s in subs if s.status in _HEALTHY_STATUSES]
            delete.extend(s.id for s in healthy[1:])
            delete.extend(
                s.id
                for s in subs
                if s.status not in _HEALTHY_STATUSES and s.status != _USER_REMOVED
            )
            if not healthy and not any(s.status == _USER_REMOVED for s in subs):
                create.append((sub_type, user_id))

    return SubscriptionPlan(create=create, delete=delete)


def format_missed_start_note(started_at: datetime, now: datetime) -> str:
    """Describe a stream whose start announcement was missed.

    Args:
        started_at: When the stream started, timezone-aware.
        now: The current time, timezone-aware.

    Returns:
        A one-line note with the elapsed time, for the announcement footer.
    """
    minutes = int((now - started_at).total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        elapsed = f'{hours}h {minutes}m'
    elif minutes:
        elapsed = f'{minutes}m'
    else:
        elapsed = 'under a minute'
    return f'Already live for {elapsed} — the start of the stream was missed'
