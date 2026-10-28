# Twitch relay: EventSub webhooks and reconciliation — design

Approved design, mirrored from https://github.com/soksanichenko/discord-meow-bot/issues/64 (2026-10-27).

## Summary

Move the Twitch relay from the EventSub **WebSocket** transport to EventSub **webhooks**, and add a periodic reconciliation of live sessions and subscriptions. Remove the user-OAuth machinery (`/twitch-relay authorize`, `twitch_auth` table, `ENCRYPTION_KEY`), which is no longer needed.

Target scale: dozens of guilds and about 100 distinct Twitch channels (about 200 subscriptions).

## Why

### 1. The WebSocket transport cannot scale to the target

Twitch limits a WebSocket user token to `max_total_cost = 10` across all subscriptions ([Subscription limits](https://dev.twitch.tv/docs/eventsub/manage-subscriptions/#subscription-limits)). `stream.online` and `stream.offline` cost 1 each unless the broadcaster has authorized our app, which relayed third-party streamers have not. That is 2 per channel, so **at most 5 distinct channels**. Today there are 3. On the 6th channel `_subscribe_user` fails and `/twitch-relay add` saves the relay but never delivers notifications.

### 2. Stuck "live" sessions (observed 2026-10-27)

`nerzhaviyka`'s `stream.online` was posted at 09:40 UTC, but no `stream.offline` was ever processed. The stream was offline (checked externally) while the bot still held a `twitch_live_sessions` row, so `/twitch-relay force-check` answered "already has an active announcement".

Evidence:
- Loki: after `Posted stream.online for nerzhaviyka` there is no `Live session removed for nerzhaviyka`, and no handler errors or timeouts.
- Prometheus: `twitch_eventsub_connected` stayed `1` for the whole period, so the watchdog (`_check_eventsub_health`) saw nothing wrong.
- At 03:49:38 twitchAPI logged `exception while resubscribing … websocket transport subscriptions total cost exceeded` (same error on 2026-10-22 and 10-25).
- In twitchAPI 4.5.0, `EventSubWebsocket._resubscribe` resubscribes sequentially and **returns on the first exception**, so the remaining subscriptions are silently lost. `_subscription_ids` in the cog still claims everything is subscribed.
- `_cleanup_stale_sessions` runs only at startup, so nothing else reconciles a lost `stream.offline`.

The cause of the lost offline event is **inferred** from these facts, not directly proven (we could not list the live Twitch-side subscriptions). The architectural gap is certain either way: any lost event leaves a session open until the next restart.

## Goals

- Support about 100 channels without hitting EventSub limits.
- Subscriptions survive bot restarts and deploys (no resubscribe-on-reconnect failure mode).
- A lost or late event never leaves a stale live session or a silently dead subscription for more than a few minutes.
- Drop code and secrets that only existed for the user-token WebSocket flow.

## Non-goals

- No new notification features, embeds or commands.
- No change to Telegram/YouTube relays.
- No Grafana alert rules in this issue (metrics are added, alerting is a follow-up).
- No WebSocket fallback or dual-transport mode. The switch is total.

## Verified against the docs (and what is not)

Checked in the official docs ([handling webhook events](https://dev.twitch.tv/docs/eventsub/handling-webhook-events/), [manage subscriptions](https://dev.twitch.tv/docs/eventsub/manage-subscriptions/), [handling WebSocket events](https://dev.twitch.tv/docs/eventsub/handling-websocket-events/)) and in the twitchAPI 4.5.0 source:

- Webhook subscriptions require an **app access token**. `stream.online/offline` need no broadcaster authorization and cost 1 each.
- `get_streams` (app or user), `get_users` (app), `get_videos` (app) work with an app token.
- Callback must be HTTPS on port 443 with a valid, non-self-signed certificate (a reverse proxy is fine).
- Signature: HMAC-SHA256 over `Message-Id + Message-Timestamp + raw body`, compared in constant time. Delivery is at-least-once, so duplicates are possible.
- Respond within a few seconds, otherwise the subscription becomes `notification_failures_exceeded` and is revoked. Subscriptions do not expire; Twitch may revoke them (`user_removed`, `authorization_revoked`, `notification_failures_exceeded`, `version_removed`).
- Webhook callback is passed per subscription; nothing has to be registered in the Developer Console.

**Not verified, to be checked during implementation:**
- The actual `max_total_cost` for our app (docs only show 10000 in example responses). Log it at startup from the API response.
- Helix rate limit for creating about 200 subscriptions in a row.
- That `get_streams` accepts 100 `user_id` values per call.
- The recommended replay window for `Message-Timestamp`.

## Design

### Decisions (the implementer's calls are marked)

1. **Own webhook handler, not `twitchAPI.eventsub.webhook.EventSubWebhook`** *(call)*. The library generates a random secret on every start and maps subscription id → callback only in memory. After a restart, subscriptions that still live at Twitch deliver events that the library answers with 200 and drops ("unknown subscription"). A `conflict` on resubscribe would also look like "already subscribed". It also compares signatures with `==`. A thin aiohttp handler with a persistent secret avoids all of this and routes by `subscription.type` + `event.broadcaster_user_id` from the body.
2. **Reuse the existing aiohttp server** (`HEALTH_PORT`, `main.py:_start_health_server`) and add `POST /twitch/eventsub`. Only that path is exposed through nginx, so `/health` and `/metrics` stay internal.
3. **Keep `twitchAPI`** as the Helix client (`get_streams`, `get_users`, `get_videos`, EventSub subscription CRUD), authenticated with the app token only (`Twitch(client_id, client_secret)`; the library refreshes the app token itself).
4. **Persistent secret** from config; subscriptions are created once and survive deploys. Rotation is done by `/twitch-relay sync` (recreate).
5. **Reconciliation is the source of truth**, run at startup and then every `TWITCH_RECONCILE_INTERVAL_MINUTES` (default 5, same style as the Telegram/YouTube pollers).

### Webhook endpoint

`POST /twitch/eventsub` (public path `/discord-bot/twitch/eventsub`):

- Read the raw body, require the `Twitch-Eventsub-*` headers, verify HMAC with `hmac.compare_digest`; any failure returns 403 (or 400 for malformed) and logs without the body or secret. Reject messages whose timestamp is outside a sane window (confirm the window in the docs).
- `webhook_callback_verification` → 200, `text/plain`, body is exactly the challenge.
- `notification` → respond 2xx **immediately**, process in a task on the bot loop (this also removes the `_dispatch`/cross-thread-loop workaround, because the handler already runs on the bot loop).
- `revocation` → 2xx, then recreate the subscription unless the reason is `user_removed` (log it).
- De-duplicate by `Message-Id` with a bounded in-memory set; handlers are also idempotent (`_post_live_notification` already refuses a second session for the same relay).
- Fail closed: if `TWITCH_EVENTSUB_SECRET` or `TWITCH_EVENTSUB_CALLBACK_URL` is missing, the route is not registered and the cog logs that the Twitch relay is disabled.

### Subscription reconciliation

Desired set = `{stream.online, stream.offline}` × distinct `twitch_user_id` in `twitch_relays`.

- List current subscriptions (`get_eventsub_subscriptions`), keep only `transport.method == webhook` with our callback URL.
- Create missing ones; delete ones without a relay; recreate ones whose status is not `enabled` (except `user_removed`).
- Bounded concurrency (small semaphore) with backoff on 429 using the `Ratelimit-Reset` header.
- The HTTP server must already be serving before subscriptions are created (Twitch calls the callback for the challenge). Run reconcile as a background task, never inside `cog_load`.
- `relay_add` ensures subscriptions for the channel; `relay_remove` deletes them when the last relay for that channel goes away (existing semantics of `_unsubscribe_user`).
- `/twitch-relay sync [channel]` now means "delete and recreate" for all or one channel (this is also the secret-rotation path).
- Log `total_cost` / `max_total_cost` at startup.

### Live-state reconciliation (safety net for lost events)

Same schedule, batched `get_streams` over all relayed channels:

- Channel **not live** but a `TwitchLiveSession` exists → edit the announcement to the "stream ended" embed (with VOD link) and delete the session. This is the existing `_cleanup_stale_sessions` logic, now run periodically instead of only at startup.
- On a Helix error, change nothing.
- Channel **live** but no session (a lost `stream.online`, e.g. during a deploy, or a relay added while the channel is already live) → post the announcement through `_post_live_notification` **with a note that the start was missed and how long the stream has already been running** (computed from the stream's `started_at`, e.g. "Already live for 1h 23m — the start of the stream was missed"). There is no age cut-off: the note makes a late announcement honest. The note is shown only on announcements posted by the reconcile, never on the normal `stream.online` path, and `/twitch-relay force-check` posts it too. *(Elapsed-time wording and where it sits in the embed are the implementer's call.)* Side effect worth knowing: a relay added for a channel that is already live gets this announcement on the next reconcile tick (at most `TWITCH_RECONCILE_INTERVAL_MINUTES` later).

### Removal

- WebSocket code: `EventSubWebsocket`, `_subscribe_user`/`_unsubscribe_user` WebSocket parts, `_check_eventsub_health`, `_is_eventsub_connected`, `_dispatch`/`_run_with_timeout` thread-loop workaround, the `twitch_eventsub_connected` metric.
- `/twitch-relay authorize`, `_poll_device_auth`, `sources/lib/db/operations/twitch_auth.py`, `TwitchAuth` model, `sources/lib/utils/crypto.py` (its only users are `twitch_auth.py` and the cog; re-check with grep before deleting), `ENCRYPTION_KEY` in `config.py`, `docker.yml`, both inventories' `group_vars`, `tests/conftest.py`, README and CLAUDE.md.
- **Alembic migration** dropping `twitch_auth`; `down_revision` is the current single head (`3c9e1f7a2b4d`, verify with `alembic heads`). `downgrade()` recreates the table structure; tokens are not restorable, so a rollback to the old code needs `/twitch-relay authorize` again.

### Metrics

Replace `twitch_eventsub_connected` with: gauges for desired and enabled webhook subscriptions, a counter of webhook requests by `type` and `result` (`ok`, `bad_signature`, `duplicate`, `malformed`), a counter of reconcile errors, and a gauge for `total_cost` vs `max_total_cost`. Reuse `relay_posts` / `relay_fetch_errors` as today.

## Twitch credentials

**Nothing new has to be issued by Twitch.**

| What | Source | Status |
|---|---|---|
| Client ID | Existing Twitch app (Infisical `twitch-client-id-discord-bot`) | already present |
| Client Secret | Existing Twitch app (Infisical `twitch-secret-discord-bot`) | already present; used for the client-credentials **app access token** (the library obtains and refreshes it) |
| EventSub secret | **Generated by us**, not by Twitch. 10–100 ASCII characters; generate with `openssl rand -hex 32`. New Infisical secret `twitch-eventsub-secret-discord-bot` → env `TWITCH_EVENTSUB_SECRET`. Twitch signs every delivery with it. | **new** |
| Callback URL | Public config, not a secret: `https://zelgray.work/discord-bot/twitch/eventsub` → env `TWITCH_EVENTSUB_CALLBACK_URL` | **new** |
| User OAuth token (device-code flow) | Stored encrypted in `twitch_auth` | **removed** |
| `ENCRYPTION_KEY` (Infisical `discord-bot-encryption-key`) | only encrypted that token | **removed** (delete the Infisical secret after the migration is deployed) |

No redirect URL or callback has to be registered in the Twitch Developer Console. After the migration, optionally revoke the previously authorized user token in the Twitch account's Connections settings.

## Infrastructure (ansible, this repo)

- `ansible/roles/discord-meow-bot-nginx`: add back a location template (the removed `discord-bot-locations.conf.j2` had the right shape) for **exactly** `= /discord-bot/twitch/eventsub`, `proxy_pass http://discord-meow-bot:{{ health_port }}/twitch/eventsub`, POST only (`limit_except POST { deny all; }`), small `client_max_body_size`, short proxy timeouts; the old delete task for `discord-bot-locations.conf` must be removed or it will delete the new file. Re-add the `health_port` default to the role. Notify `Restart Nginx container`.
- `deploy_bot_container/tasks/docker.yml`: add `TWITCH_EVENTSUB_SECRET` and `TWITCH_EVENTSUB_CALLBACK_URL`, remove `ENCRYPTION_KEY`.
- `inventories/zelgray.work/group_vars/all.yml`: add the Infisical lookup for the new secret and the callback URL, remove `encryption_key`.
- `inventories/home-server`: **the Twitch webhook is not needed there**, so the Twitch relay stays disabled on that host (the endpoint is not registered when the secret or callback URL is empty). Remove `encryption_key` there as well, and make sure `docker.yml` does not fail on the undefined variables for this inventory (for example `{{ twitch_eventsub_secret | default('') }}` or empty values in its `group_vars`). Verify with `--syntax-check` and `--check` against both inventories.
- YAML conventions: every file starts with `---` and ends with `...`.

## Implementation plan (TDD, tests first at each step)

Two PRs so the risky part is small and the first one changes no behaviour.

**PR 1 – infrastructure and config (no behaviour change)**
1. `config.py`: add `twitch_eventsub_secret: SecretStr`, `twitch_eventsub_callback_url: str` (https validation), `twitch_reconcile_interval_minutes: int = 5`. Tests for validation.
2. Ansible: nginx location, env vars, Infisical lookups (both inventories). Verify with `ansible-playbook --syntax-check` and `--check --tags nginx`.
3. Create the Infisical secret, then `./deploy.sh --tags nginx`. Verify: `GET` on the path → 405, `POST` without a signature → 4xx from the bot or 404 until PR 2 (never 502 once the bot is up).

**PR 2 – bot code and migration**
1. Tests, then implementation of the pure pieces: signature verification, handler (challenge/notification/revocation/duplicate/oversized/malformed), subscription diff function (table-driven: missing, orphan, non-enabled, `user_removed`, foreign callback, duplicates), live-state diff.
2. Webhook route registered in `main.py` next to `/health`; cog wiring (`cog_load` no longer authenticates a user, starts the reconcile job and an initial background reconcile).
3. Regression test for the 2026-10-27 incident: a live session whose offline event was lost is closed by the next reconcile, and `force-check` then no longer reports an active announcement. Also tests for the missed-start note: shown with the correct elapsed time when the reconcile announces a stream, absent on the normal `stream.online` path, and no duplicate announcement when a session already exists.
4. Remove WebSocket code, `authorize`, `twitch_auth` ops/model/crypto, `ENCRYPTION_KEY`; update or delete the affected tests (`tests/cogs/test_twitch_relay.py`, `tests/cogs/test_permissions.py`, `tests/db/*`, `tests/conftest.py`).
5. Alembic migration dropping `twitch_auth`: single head (`alembic -c sources/alembic.ini heads`), `DISCORD_TOKEN=dummy alembic -c sources/alembic.ini upgrade head` against the dev DB, plus a downgrade/upgrade cycle.
6. Docs: README (config table), CLAUDE.md (config table, models, command table, health/metrics, deployment notes).
7. Local end-to-end check with the Twitch CLI against the handler (`twitch event verify-subscription` / `trigger`, see the webhook docs) before deploying.

## Rollout and verification

1. Infisical secret created, PR 1 deployed (nginx first).
2. PR 2 via `./deploy.sh`. The migration runs on container start, so this is the point of no return for the old code.
3. Check: log lines for the created subscriptions, `get_eventsub_subscriptions` shows 2 × channels `enabled` with our callback, new Prometheus gauges match, `max_total_cost` logged.
4. Live test with a relayed channel: online post, offline edit with VOD link, session removed. Then `/twitch-relay force-check` reports "not currently live".
5. Restart the container once and confirm no resubscription storm and that a delivery after the restart is still processed (this is the main reason for the persistent secret).
6. Cleanup: delete the obsolete Infisical `discord-bot-encryption-key`.

Rollback: redeploy the previous image, `alembic downgrade -1`, run `/twitch-relay authorize`. Webhook subscriptions created at Twitch should be deleted (`/twitch-relay remove` or the reconcile on the new code before rollback), otherwise Twitch keeps calling the endpoint and eventually revokes them.

## Risks

| Risk | Mitigation |
|---|---|
| Events lost during the restart window of a deploy | Twitch retries failed deliveries (verify the exact policy); the startup and periodic reconcile closes stale sessions and announces missed live streams with the "start was missed" note |
| Secret leak or mismatch | `/twitch-relay sync` recreates all subscriptions; reconcile recreates `notification_failures_exceeded` ones |
| Public endpoint abuse | HMAC check before any parsing of content, exact-path nginx location, POST only, body size limit, no body logging |
| Helix rate limits at 200 creations | Bounded concurrency and backoff; verify limits during implementation |
| Callback unreachable during subscribe (nginx/cert) | Reconcile retries on the next tick; startup log shows the failure reason |
| Migration is one-way for tokens | Documented above; acceptable because the token is no longer needed |

## Decisions made

1. Missed-`online` announcements: the reconcile posts them with a note about the missed start and the elapsed time (no age cut-off).
2. `home-server` inventory: no public callback; the Twitch relay stays disabled there.

No open questions remain, apart from the items listed under "Not verified" that are settled during implementation.
