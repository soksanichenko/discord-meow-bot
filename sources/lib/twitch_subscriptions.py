"""Keep Twitch EventSub webhook subscriptions in step with the configured relays."""

import asyncio

from twitchAPI.twitch import Twitch

from sources.lib.utils.logger import Logger
from sources.lib.utils.metrics import (
    twitch_subscription_cost,
    twitch_subscriptions_desired,
    twitch_subscriptions_enabled,
)
from sources.lib.utils.twitch_eventsub import (
    SUBSCRIPTION_TYPES,
    Subscription,
    plan_subscriptions,
)


class TwitchSubscriptionManager:
    """Create, delete and recreate our EventSub webhook subscriptions via Helix.

    Subscriptions live at Twitch and survive bot restarts, so the manager works
    by comparing what Twitch lists with what the relays require.
    """

    def __init__(
        self, twitch: Twitch, callback_url: str, secret: str, concurrency: int = 5
    ) -> None:
        """Initialise the manager.

        Args:
            twitch: A Twitch client authenticated with an app access token.
            callback_url: The public HTTPS URL Twitch delivers events to.
            secret: The EventSub secret Twitch signs deliveries with.
            concurrency: Maximum number of simultaneous Helix write calls.
        """
        self.logger = Logger()
        self._twitch = twitch
        self._callback_url = callback_url
        self._secret = secret
        self._semaphore = asyncio.Semaphore(concurrency)

    async def list_subscriptions(self) -> list[Subscription]:
        """List our webhook subscriptions and publish the cost gauges.

        Returns:
            Subscriptions of a type we manage whose callback is our own URL.
        """
        result = await self._twitch.get_eventsub_subscriptions()
        twitch_subscription_cost.labels(kind='total').set(result.total_cost)
        twitch_subscription_cost.labels(kind='max').set(result.max_total_cost)
        subs = []
        async for sub in result:
            if (
                sub.type in SUBSCRIPTION_TYPES
                and sub.transport.get('method') == 'webhook'
                and sub.transport.get('callback') == self._callback_url
            ):
                subs.append(
                    Subscription(
                        id=sub.id,
                        type=sub.type,
                        user_id=sub.condition.get('broadcaster_user_id', ''),
                        status=sub.status,
                    )
                )
        return subs

    async def reconcile(self, desired_user_ids: set[str]) -> set[str]:
        """Make Twitch's subscriptions match the channels that have a relay.

        Args:
            desired_user_ids: Twitch user ids that have at least one relay.

        Returns:
            User ids for which a create or delete failed; the next reconcile
            retries them.
        """
        current = await self.list_subscriptions()
        plan = plan_subscriptions(desired_user_ids, current)
        owner = {s.id: s.user_id for s in current}
        failed = await self._apply(
            [(sub_id, owner[sub_id]) for sub_id in plan.delete], plan.create
        )
        twitch_subscriptions_desired.set(
            len(desired_user_ids) * len(SUBSCRIPTION_TYPES)
        )
        if plan.create or plan.delete:
            current = await self.list_subscriptions()
        twitch_subscriptions_enabled.set(
            sum(1 for s in current if s.status == 'enabled')
        )
        return failed

    async def recreate(self, user_ids: set[str]) -> set[str]:
        """Delete and recreate every subscription of the given channels.

        This is also how the EventSub secret is rotated.

        Args:
            user_ids: Twitch user ids to recreate subscriptions for.

        Returns:
            User ids for which a create or delete failed.
        """
        current = await self.list_subscriptions()
        deletes = [(s.id, s.user_id) for s in current if s.user_id in user_ids]
        creates = [(t, uid) for uid in sorted(user_ids) for t in SUBSCRIPTION_TYPES]
        return await self._apply(deletes, creates)

    async def _apply(
        self, deletes: list[tuple[str, str]], creates: list[tuple[str, str]]
    ) -> set[str]:
        """Run deletes, then creates, each batch concurrently within the limit.

        Deleting first matters: Twitch rejects a second subscription with the
        same type, condition and transport while the old one still exists.
        """
        failed: set[str] = set()
        await asyncio.gather(*(self._delete(sid, uid, failed) for sid, uid in deletes))
        await asyncio.gather(*(self._create(t, uid, failed) for t, uid in creates))
        return failed

    async def _delete(self, sub_id: str, user_id: str, failed: set[str]) -> None:
        async with self._semaphore:
            try:
                await self._twitch.delete_eventsub_subscription(sub_id)
            except Exception as exc:
                failed.add(user_id)
                self.logger.warning(
                    'Failed to delete Twitch subscription %s (%s): %s',
                    sub_id,
                    type(exc).__name__,
                    exc,
                )

    async def _create(self, sub_type: str, user_id: str, failed: set[str]) -> None:
        transport = {
            'method': 'webhook',
            'callback': self._callback_url,
            'secret': self._secret,
        }
        async with self._semaphore:
            try:
                await self._twitch.create_eventsub_subscription(
                    sub_type, '1', {'broadcaster_user_id': user_id}, transport
                )
            except Exception as exc:
                failed.add(user_id)
                self.logger.warning(
                    'Failed to create %s subscription for %s (%s): %s',
                    sub_type,
                    user_id,
                    type(exc).__name__,
                    exc,
                )
