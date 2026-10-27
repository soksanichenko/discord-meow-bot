# Twitch EventSub Webhooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Twitch EventSub WebSocket transport with webhooks, add periodic subscription and live-session reconciliation, and remove the user-OAuth machinery.

**Architecture:** A thin aiohttp handler (`POST /twitch/eventsub` on the existing health server) verifies Twitch's HMAC with a persistent secret, acks immediately and processes events as tasks on the bot loop. A `TwitchSubscriptionManager` keeps Helix subscriptions equal to `{stream.online, stream.offline} × relayed channels`. A reconcile job (startup + every N minutes) also compares `get_streams` with `twitch_live_sessions`: it closes stale sessions and announces missed live streams with a "start was missed" note.

**Tech Stack:** Python 3.12, aiohttp 3.14, twitchAPI 4.5.0 (Helix client only), SQLAlchemy async, APScheduler, Alembic, Ansible, pytest (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-10-27-twitch-eventsub-webhooks-design.md` (GitHub issue #64). Read it first; this plan only records the decisions the spec leaves to the implementer.

## Global Constraints

- Python 3.12; single quotes (docstrings use `"""`); type annotations everywhere; Google-style docstrings on every public function/class; ruff rules `E,W,F,I,UP,B,Q` must pass (`ruff check sources/`, `ruff format --check sources/`).
- Logging only through `Logger()` from `sources.lib.utils.logger`, `%`-formatting, never f-strings in log calls.
- Code, comments, commit messages, PR text: English. Commit style: `type(scope): subject`; end commit messages with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; end PR descriptions with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **Never `git push` without explicit user approval** (commit locally, show the summary, ask "Push?").
- Fresh DB session per operation (`async with AsyncSession()`); never block the event loop.
- Twitch values: subscription types `stream.online` and `stream.offline`, version `'1'`, condition `{'broadcaster_user_id': <id>}`, transport `{'method': 'webhook', 'callback': <url>, 'secret': <secret>}`. EventSub secret: ASCII, 10–100 characters. Callback URL: `https://` only.
- Paths: internal route `POST /twitch/eventsub`; public path `/discord-bot/twitch/eventsub`; production callback `https://zelgray.work/discord-bot/twitch/eventsub`.
- New env vars: `TWITCH_EVENTSUB_SECRET`, `TWITCH_EVENTSUB_CALLBACK_URL`, `TWITCH_RECONCILE_INTERVAL_MINUTES` (default 5). Infisical secret name: `twitch-eventsub-secret-discord-bot`.
- Webhook limits: body ≤ 64 KiB; respond 2xx before doing any work; `get_streams` batch size 100 (`_STREAMS_BATCH`; confirm in the Helix docs in Task 8).
- Ansible YAML: every file starts with `---` and ends with `...` plus an empty line.
- Tests: `python -m pytest tests/ -q` (integration tests need Docker and are skipped or fail without it; that is pre-existing).
- Two PRs, in this order: **PR 1** (Tasks 1–3, branch `feat/twitch-webhook-infra`, already created) and **PR 2** (Tasks 4–10, branch `feat/twitch-eventsub-webhooks` created from `main` after PR 1 is merged and deployed).

## Review Focus

Failure modes the spec implies; each has a test in the task that owns the code.

1. A correctly signed request with an old `Message-Timestamp` (replay) → 403 (Task 5).
2. Oversized or chunked body → 413 without reading it unbounded (Task 5).
3. Twitch redelivers the same `stream.online` after a bot restart (in-memory dedupe is empty) → one announcement only, even when the reconcile races the notification (Task 8, per-channel lock).
4. Helix error or timeout during live-state reconcile → no sessions closed and nothing posted (an empty result from an error must not look like "nobody is live") (Task 8).
5. A relayed channel whose Twitch account was deleted (`user_removed`), or a callback that Twitch cannot reach → reconcile keeps going for other channels, reports the failed ids, never loops creating/deleting (Tasks 4 and 6).

---

# PR 1 — infrastructure and config (no behaviour change)

### Task 1: Config settings

**Files:**
- Modify: `sources/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.twitch_eventsub_secret: SecretStr` (default empty), `Config.twitch_eventsub_callback_url: str` (default empty), `Config.twitch_reconcile_interval_minutes: int = 5`. Empty secret or URL means "Twitch relay disabled".

- [ ] **Step 1: Write failing tests in `tests/test_config.py`**
  - `test_eventsub_defaults_are_empty_and_interval_is_five`: `Config()` has `twitch_eventsub_secret.get_secret_value() == ''`, `twitch_eventsub_callback_url == ''`, `twitch_reconcile_interval_minutes == 5`.
  - `test_callback_url_must_be_https`: `Config(twitch_eventsub_callback_url='http://example.com/x')` raises `pydantic.ValidationError`; the `https://` form is accepted.
  - `test_secret_length_and_charset`: 9 chars, 101 chars and a non-ASCII value raise `ValidationError`; 10 and 100 ASCII chars are accepted; the empty string is accepted.
- [ ] **Step 2: Run** `python -m pytest tests/test_config.py -v` → FAIL (unknown fields).
- [ ] **Step 3: Implement** the three fields and two `field_validator`s in `Config` (validators accept the empty string; they enforce the rules above otherwise).
- [ ] **Step 4: Run** `python -m pytest tests/test_config.py -v` → PASS; then `python -m pytest tests/ -q` → no new failures.
- [ ] **Step 5: Commit** `feat(config): add Twitch EventSub webhook settings`.

### Task 2: Ansible — env vars and nginx location

**Files:**
- Modify: `ansible/roles/deploy_bot_container/tasks/docker.yml` (add `TWITCH_EVENTSUB_SECRET`, `TWITCH_EVENTSUB_CALLBACK_URL` to `env`; do **not** remove `ENCRYPTION_KEY` yet — that is Task 9)
- Modify: `ansible/inventories/zelgray.work/group_vars/all.yml` (`twitch_eventsub_secret` from Infisical key `twitch-eventsub-secret-discord-bot`; `twitch_eventsub_callback_url: "https://zelgray.work/discord-bot/twitch/eventsub"`)
- Modify: `ansible/inventories/home-server/group_vars/all.yml` (`twitch_eventsub_secret: ""`, `twitch_eventsub_callback_url: ""` — Twitch relay stays disabled there)
- Create: `ansible/roles/discord-meow-bot-nginx/templates/discord-bot-locations.conf.j2`
- Modify: `ansible/roles/discord-meow-bot-nginx/tasks/main.yml`, `ansible/roles/discord-meow-bot-nginx/defaults/main.yml`

**Interfaces:**
- Produces: nginx location `= /discord-bot/twitch/eventsub` → `http://discord-meow-bot:{{ health_port }}/twitch/eventsub`; container env vars above.

- [ ] **Step 1: Template.** Exact-match `location = /discord-bot/twitch/eventsub` with `limit_except POST { deny all; }`, `client_max_body_size 64k;`, `proxy_pass http://discord-meow-bot:{{ health_port }}/twitch/eventsub;`, `proxy_connect_timeout 3s;`, `proxy_read_timeout 10s;`. No auth_basic (Twitch cannot send it; the HMAC is the authentication).
- [ ] **Step 2: Tasks.** In `tasks/main.yml` delete only the `discord-bot-locations.conf` entry from the "Remove the public health endpoint…" loop (keep the htpasswd entry and fix the comment above it), and add a task that templates `discord-bot-locations.conf` into `{{ nginx_custom_locations_path }}` with `mode: "0644"`, `become: yes`, `notify: Restart Nginx container`. Re-add `health_port: 8080` to the role defaults.
- [ ] **Step 3: Variables.** Add the group_vars entries and the two `env` lines (values `"{{ twitch_eventsub_secret }}"` / `"{{ twitch_eventsub_callback_url }}"`).
- [ ] **Step 4: Verify.** From `ansible/`: `ansible-playbook -i inventories/zelgray.work playbooks/deploy.yml --syntax-check` and the same for `inventories/home-server` → both print the playbook name and exit 0. If `ansible-lint` is installed, run it on the changed files. If Infisical/SSH access is available, also `--check --tags nginx` on `zelgray.work`.
- [ ] **Step 5: Commit** `feat(ansible): expose the Twitch EventSub webhook path through nginx`.

### Task 3: PR 1 rollout checkpoint (needs the user)

- [ ] **Step 1: Commit** the design and this plan (`docs(twitch): add EventSub webhook design and plan`), run `ruff check sources/` and `python -m pytest tests/ -q`.
- [ ] **Step 2: Ask the user** to create the Infisical secret `twitch-eventsub-secret-discord-bot` in `/hosts/zelgray-work` (value: output of `openssl rand -hex 32`). The Ansible Infisical lookup fails the whole play if the key is missing, so this must happen before any deploy.
- [ ] **Step 3: Ask "Push?"**, then open PR 1 (`gh pr create`, base `main`), wait for CI, merge only on the user's approval.
- [ ] **Step 4: With the user's approval** run `./deploy.sh --tags nginx`. Expected: `curl -i https://zelgray.work/discord-bot/twitch/eventsub` → `405` (GET denied by `limit_except`); `curl -i -X POST https://zelgray.work/discord-bot/twitch/eventsub -d '{}'` → `404` from the bot (route not implemented yet), **not** `502` and not nginx's own 404 page.

---

# PR 2 — bot code and migration

Before Task 4: `git checkout main && git pull && git checkout -b feat/twitch-eventsub-webhooks`.

### Task 4: Pure EventSub helpers

**Files:**
- Create: `sources/lib/utils/twitch_eventsub.py`, `tests/utils/__init__.py`, `tests/utils/test_twitch_eventsub.py`

**Interfaces (Produces, used by Tasks 5, 6, 8):**
```python
SUBSCRIPTION_TYPES: tuple[str, ...] = ('stream.online', 'stream.offline')
MAX_MESSAGE_AGE: timedelta  # 10 minutes; confirm the recommended window in the Twitch docs (Step 1)

@dataclass(frozen=True)
class Subscription:  # a webhook subscription as listed by Helix
    id: str
    type: str
    user_id: str
    status: str

@dataclass(frozen=True)
class SubscriptionPlan:
    create: list[tuple[str, str]]  # (type, user_id)
    delete: list[str]              # subscription ids

def verify_signature(secret: str, message_id: str, timestamp: str, body: bytes, signature: str) -> bool
def is_timestamp_fresh(timestamp: str, now: datetime, max_age: timedelta = MAX_MESSAGE_AGE) -> bool
class MessageDeduplicator:
    def __init__(self, max_size: int = 1000) -> None
    def seen(self, message_id: str) -> bool  # records the id; True if it was already recorded
def plan_subscriptions(desired_user_ids: set[str], actual: list[Subscription]) -> SubscriptionPlan
def format_missed_start_note(started_at: datetime, now: datetime) -> str
```

- [ ] **Step 1: Check the docs.** Read the Twitch EventSub overview/webhook docs for the recommended replay window of `Twitch-Eventsub-Message-Timestamp` and set `MAX_MESSAGE_AGE` accordingly (keep 10 minutes if the docs say so or say nothing).
- [ ] **Step 2: Write failing tests** (all in `tests/utils/test_twitch_eventsub.py`):
  - `verify_signature`: true for `'sha256=' + hmac_sha256(secret, id + timestamp + body).hexdigest()`; false for tampered body, wrong secret, missing `sha256=` prefix, empty string.
  - `is_timestamp_fresh`: `'2026-10-27T10:00:00.634234626Z'` (nanoseconds, as Twitch sends) is fresh at +9 min and stale at +11 min; garbage text → `False`; a timestamp more than `max_age` in the future → `False`.
  - `MessageDeduplicator(max_size=2)`: `seen('a')` is `False` then `True`; after `a`, `b`, `c` the id `a` has been evicted (`seen('a')` is `False`).
  - `plan_subscriptions`, table-driven on `desired={'1'}`: no actual → create both types for `'1'`; both enabled → empty plan; enabled subs for user `'9'` not desired → both ids in `delete`; `notification_failures_exceeded` on `stream.online` → that id deleted **and** `('stream.online','1')` created; `webhook_callback_verification_pending` → kept; `user_removed` → neither deleted nor created for that key; two enabled subs for one key → the one with the greater id is deleted.
  - `format_missed_start_note`: 83 min → `'Already live for 1h 23m — the start of the stream was missed'`; 5 min → `'Already live for 5m — …'`; 20 s → `'Already live for under a minute — …'`; 25 h 3 m → `'Already live for 25h 3m — …'`.
- [ ] **Step 3: Run** `python -m pytest tests/utils/test_twitch_eventsub.py -v` → FAIL (module missing).
- [ ] **Step 4: Implement** the module. Signature check with `hmac.compare_digest`; timestamp parsing with `datetime.fromisoformat` (3.12 truncates nanoseconds); the deduplicator is a bounded `OrderedDict`; "healthy" statuses are `enabled` and `webhook_callback_verification_pending`.
- [ ] **Step 5: Run** the new tests → PASS; `ruff check sources/ tests/utils` clean.
- [ ] **Step 6: Commit** `feat(twitch): add EventSub signature, dedupe and subscription-plan helpers`.

### Task 5: Webhook handler and route

**Files:**
- Create: `sources/lib/twitch_webhook.py`, `tests/test_twitch_webhook.py`
- Modify: `sources/lib/utils/metrics.py` (add `twitch_webhook_requests = Counter('twitch_webhook_requests', '…', ['type', 'result'])`; the old `twitch_eventsub_connected` stays until Task 7)
- Modify: `sources/scripts/main.py` (register the route in `_start_health_server`)

**Interfaces:**
- Consumes: Task 4 helpers.
- Produces:
```python
@dataclass(frozen=True)
class StreamEvent:
    user_id: str
    login: str
    display_name: str

class TwitchWebhookHandler:
    def __init__(
        self,
        secret: str,
        on_online: Callable[[StreamEvent], Awaitable[None]],
        on_offline: Callable[[StreamEvent], Awaitable[None]],
        on_revocation: Callable[[Subscription], Awaitable[None]],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None
    async def handle(self, request: web.Request) -> web.Response
    async def wait_for_pending(self) -> None  # awaits in-flight event tasks (tests, shutdown)

async def eventsub_route(request: web.Request) -> web.Response
# looks up request.app['bot'].get_cog('TwitchRelayCog'); 404 if it is missing or its
# `webhook_handler` is None; otherwise `return await cog.webhook_handler.handle(request)`
```
- Response contract: bad/missing signature or stale timestamp → 403; body over 64 KiB → 413; malformed JSON or unknown `Twitch-Eventsub-Message-Type` → 400 (checked **after** the signature); `webhook_callback_verification` → 200 `text/plain` with exactly the challenge; `notification` and `revocation` → 204; duplicate `Message-Id` → 204 without processing; unknown subscription type → 204 ignored.

- [ ] **Step 1: Write failing tests** with `aiohttp.test_utils.TestClient(TestServer(app))` (no new dependency), a `_signed(secret, payload, message_type, message_id='m1', timestamp=None)` helper that builds headers and raw body, and AsyncMocks as callbacks:
  - challenge → 200, `text/plain`, body `'abc'`; challenge with wrong signature → 403.
  - `stream.online` notification → 204 and, after `wait_for_pending()`, `on_online` awaited once with `StreamEvent('123', 'streamer', 'Streamer')`; `stream.offline` → `on_offline`.
  - same `Message-Id` twice → callback awaited once, second response 204.
  - wrong signature → 403, no callback; valid signature with timestamp 11 minutes old (injected `now`) → 403.
  - malformed JSON with valid signature → 400; unknown message type with valid signature → 400.
  - 64 KiB + 1 byte body → 413 and the callbacks never run.
  - revocation with `status: 'authorization_revoked'` → 204 and `on_revocation` awaited with `Subscription(id=…, type='stream.online', user_id='123', status='authorization_revoked')`.
  - `on_online` raising → response still 204 and the exception is logged (no unhandled-task warning).
  - counter: after one good notification `twitch_webhook_requests.labels(type='notification', result='ok')` increased by 1; after a bad signature `result='bad_signature'` increased by 1.
  - `eventsub_route`: 404 when `get_cog` returns `None` and when `webhook_handler` is `None`; delegates to `handle` otherwise.
- [ ] **Step 2: Run** `python -m pytest tests/test_twitch_webhook.py -v` → FAIL.
- [ ] **Step 3: Implement** `TwitchWebhookHandler.handle` (read at most 64 KiB + 1 from `request.content`; verify signature → verify timestamp → parse; keep strong references to spawned tasks in a set and log task exceptions through `Logger()`; never log bodies or the secret) and `eventsub_route`; add the metric; in `_start_health_server` add `app.router.add_post('/twitch/eventsub', eventsub_route)` with a one-line comment that only this path is proxied by nginx.
- [ ] **Step 4: Run** the tests → PASS; full suite no regressions.
- [ ] **Step 5: Commit** `feat(twitch): add EventSub webhook handler and route`.

### Task 6: Subscription manager

**Files:**
- Create: `sources/lib/twitch_subscriptions.py`, `tests/test_twitch_subscriptions.py`
- Modify: `sources/lib/utils/metrics.py` (gauges `twitch_subscriptions_desired`, `twitch_subscriptions_enabled`, and `twitch_subscription_cost` labelled `kind` = `total` | `max`)

**Interfaces:**
- Consumes: `Subscription`, `SubscriptionPlan`, `plan_subscriptions`, `SUBSCRIPTION_TYPES` from Task 4.
- Produces:
```python
class TwitchSubscriptionManager:
    def __init__(self, twitch: Twitch, callback_url: str, secret: str, concurrency: int = 5) -> None
    async def list_subscriptions(self) -> list[Subscription]
    async def reconcile(self, desired_user_ids: set[str]) -> set[str]   # returns user ids with a failed create/delete
    async def recreate(self, user_ids: set[str]) -> set[str]            # delete ours, then create; returns failed user ids
```
`list_subscriptions` returns only subscriptions with `transport['method'] == 'webhook'`, `transport['callback'] == callback_url` and a type in `SUBSCRIPTION_TYPES`, and sets the gauges from `total_cost` / `max_total_cost` of the Helix result. Helix calls: `twitch.get_eventsub_subscriptions()` (iterate it to page), `twitch.create_eventsub_subscription(type, '1', {'broadcaster_user_id': uid}, transport)`, `twitch.delete_eventsub_subscription(sub_id)`.

- [ ] **Step 1: Check Helix limits.** Read the Helix "Create EventSub Subscription" reference and note the rate-limit cost; twitchAPI has no 429 retry (grep found none). Decision for this plan: no backoff beyond the semaphore — a failed create is reported in the returned set and retried by the next reconcile tick. Add `Ratelimit-Reset` backoff only if the docs show 200 sequential creates can hit the limit.
- [ ] **Step 2: Write failing tests** with a mocked `Twitch` (`get_eventsub_subscriptions` returns an async-iterable `SimpleNamespace`-based result with `total_cost`, `max_total_cost`):
  - `list_subscriptions` ignores a foreign callback, a websocket transport and an unrelated type; sets `twitch_subscription_cost` gauges.
  - `reconcile({'123'})` with nothing listed calls `create_eventsub_subscription` exactly twice with `('stream.online','1',{'broadcaster_user_id':'123'},{'method':'webhook','callback':URL,'secret':SECRET})` and the same for `stream.offline`; returns `set()`.
  - `reconcile(set())` with two listed subs for user `'9'` deletes both ids.
  - one create raising → the other users are still processed and the failed user id is in the result.
  - `reconcile` with a `user_removed` subscription neither creates nor deletes for that key.
  - `recreate({'123'})` deletes the two listed ids, then creates both; failure → user id returned.
  - concurrency: with 20 users and `concurrency=5`, the maximum number of simultaneous Helix calls observed by the fake is ≤ 5.
- [ ] **Step 3: Run** → FAIL. **Step 4: Implement** (use `plan_subscriptions`; apply the plan under an `asyncio.Semaphore`; log failures with `Logger()`). **Step 5: Run** → PASS.
- [ ] **Step 6: Commit** `feat(twitch): manage EventSub webhook subscriptions through Helix`.

### Task 7: Cog — switch from WebSocket to webhooks

**Files:**
- Modify: `sources/lib/cogs/twitch_relay.py`, `sources/lib/utils/metrics.py` (remove `twitch_eventsub_connected`), `tests/cogs/test_twitch_relay.py`

**Interfaces:**
- Consumes: Tasks 4–6.
- Produces on `TwitchRelayCog`: attributes `webhook_handler: TwitchWebhookHandler | None`, `_manager: TwitchSubscriptionManager | None`; `async def _desired_user_ids(self) -> set[str]` (distinct `twitch_user_id` over `get_all_relays()`); `async def _reconcile(self) -> None` (runs `self._manager.reconcile(...)`, then `_reconcile_live_state()` which Task 8 adds — until then a stub that does nothing); scheduler job id `twitch_reconcile` every `config.twitch_reconcile_interval_minutes`; `_handle_stream_online(event: StreamEvent)` and `_handle_stream_offline(event: StreamEvent)` (same logic as today, now reading `event.user_id/login/display_name`).

- [ ] **Step 1: Update tests first.** In `tests/cogs/test_twitch_relay.py`: change `_stream_online_event` to build `StreamEvent(user_id, login, display_name)`; delete `TestIsEventsubConnected`; add `TestCogLoad` (patch `config` and `Twitch`): with any of client id / client secret / EventSub secret / callback URL empty → `cog._twitch is None` and `webhook_handler is None`; with all set → `webhook_handler` and `_manager` exist and the scheduler has job `twitch_reconcile`; `cog_unload` closes the Twitch client. Add one command test each: `relay_add` calls `_manager.reconcile` and reports failure when its returned set contains the user id; `relay_remove` calls `_manager.reconcile` after removing; `relay_sync` calls `_manager.recreate({user_id})` and renders ✓/✗ per channel from the returned set.
- [ ] **Step 2: Run** `python -m pytest tests/cogs/test_twitch_relay.py -q` → the new/changed tests FAIL.
- [ ] **Step 3: Implement.** `cog_load`: app authentication only (`await Twitch(client_id, client_secret)`; no `set_user_authentication`, no `get_auth`); build handler (callbacks wrap `_handle_stream_online/offline` in the existing `_run_with_timeout(…, timeout=60, retries=2, retry_delay=5)`; `on_revocation` logs and triggers `_reconcile`) and manager; start the scheduler; `asyncio.create_task(self._reconcile())` for the initial run (never inside `cog_load` itself). Replace `_subscribe_user`/`_unsubscribe_user` usage in `relay_add`/`relay_remove`/`relay_sync`/`relay_modify` with the manager. Delete `EventSubWebsocket` import and code, `_subscription_ids`, `_eventsub`, `_check_eventsub_health`, `_is_eventsub_connected`, `_init_subscriptions`, `_on_stream_online/_offline`, `_dispatch`, `_task_done_callback`, the `_EVENTSUB_WATCHDOG_MINUTES` constant and the old metric. Update the module/class docstrings (WebSocket → webhook). Leave `_cleanup_stale_sessions` alone (Task 8 replaces it).
- [ ] **Step 4: Run** `python -m pytest tests/ -q` → PASS (including `tests/cogs/test_permissions.py` and `test_cog_discord.py`).
- [ ] **Step 5: Commit** `feat(twitch-relay): receive stream events through EventSub webhooks`.

### Task 8: Live-state reconcile, missed-start note, per-channel lock

**Files:**
- Modify: `sources/lib/cogs/twitch_relay.py`, `tests/cogs/test_twitch_relay.py`

**Interfaces:**
- Consumes: `format_missed_start_note` (Task 4); `_reconcile` from Task 7.
- Produces: `_STREAMS_BATCH = 100`; `async def _reconcile_live_state(self) -> None` (replaces `_cleanup_stale_sessions`); `_post_live_notification(..., missed_start_note: str | None = None)`; `_build_live_embed(..., missed_start_note: str | None = None)` (the note becomes the embed footer); `_fetch_stream_info` result gains key `'started_at': datetime`; `self._channel_locks: defaultdict[str, asyncio.Lock]` held by `_post_live_notification` for the whole check-existing → send → `add_live_session` section.

- [ ] **Step 1: Check the docs** that `get_streams` accepts up to 100 `user_id` values per request; adjust `_STREAMS_BATCH` if not.
- [ ] **Step 2: Write failing tests** (reusing the existing helpers/patch style):
  - `test_reconcile_closes_session_when_channel_not_live` — regression for the 2026-10-27 incident: one session (`discord_message_id=555`), `get_streams` yields nothing → the original message is edited with the "Stream ended" embed and `remove_live_session(session.id)` is awaited.
  - `test_reconcile_keeps_session_when_channel_live`.
  - `test_reconcile_changes_nothing_when_get_streams_raises` — no edit, no removal, no send.
  - `test_reconcile_batches_user_ids_by_100` — 150 relays → two `get_streams` calls with 100 and 50 ids, results unioned.
  - `test_reconcile_announces_live_channel_without_session_with_note` — `_fetch_stream_info` returns `started_at = now − 83 min − 30 s`; the posted embed's footer text equals `'Already live for 1h 23m — the start of the stream was missed'` and `add_live_session` is awaited.
  - `test_stream_online_notification_has_no_missed_start_note` — footer is `None`.
  - `test_force_check_post_includes_missed_start_note`.
  - `test_concurrent_notifications_for_one_relay_post_once` — two concurrent `_post_live_notification` calls with a stateful fake `get_live_sessions_for_user`/`add_live_session` → `channel.send` awaited exactly once.
- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Implement.** `_reconcile_live_state`: load sessions and relays; fetch live streams in chunks of `_STREAMS_BATCH`, aborting the whole run (log, change nothing) on any exception; close sessions whose channel is not live by extracting the body of the old `_cleanup_stale_sessions` loop into `_close_session(session, relay)`; for live channels with a relay but no session call `_fetch_stream_info(uid, max_attempts=1)` and `_post_live_notification` with the note. Delete `_cleanup_stale_sessions` and its call. Set `relay_last_poll.labels(service='twitch')` the same way the YouTube relay does. Make `relay_force_check` pass the note too.
- [ ] **Step 5: Run** `python -m pytest tests/ -q` → PASS.
- [ ] **Step 6: Commit** `feat(twitch-relay): reconcile live sessions and announce missed streams`.

### Task 9: Remove the user-OAuth flow and the migration

**Files:**
- Delete: `sources/lib/db/operations/twitch_auth.py`, `sources/lib/utils/crypto.py`
- Modify: `sources/lib/cogs/twitch_relay.py` (remove `relay_authorize`, `_poll_device_auth`, `_TOKEN_URL`, `_DEVICE_URL`, `_MAX_POLL_SECONDS`, `_REQUEST_TIMEOUT`, `_http_session`, `_on_token_refresh`, `get_auth`/`save_auth` imports), `sources/lib/db/models.py` (remove `TwitchAuth`), `sources/config.py` (remove `encryption_key`), `sources/requirements.txt` (remove `cryptography` after `grep -rn cryptography` shows no remaining users), `tests/conftest.py` (remove the `ENCRYPTION_KEY` stub), `tests/db/test_db_operations.py` and `tests/db/test_db_integration.py` (remove the `twitch_auth` and `InvalidToken` tests), `tests/cogs/test_twitch_relay.py` (remove authorize tests), `ansible/roles/deploy_bot_container/tasks/docker.yml` and both `group_vars/all.yml` (remove `ENCRYPTION_KEY` / `encryption_key`)
- Create: `sources/lib/db/alembic/versions/<id>_drop_twitch_auth_table.py`

**Interfaces:**
- Produces: Alembic revision with `down_revision = '3c9e1f7a2b4d'` (confirm with `alembic -c sources/alembic.ini heads` first). `upgrade()` drops `twitch_auth`; `downgrade()` recreates it exactly as `e4f5a6b7c8d9_recreate_twitch_auth_table.py` does (columns `id`, `access_token`, `refresh_token`, `expires_at`).

- [ ] **Step 1: Write the migration** with `alembic -c sources/alembic.ini revision -m 'drop twitch_auth table'` and fill in `upgrade`/`downgrade`.
- [ ] **Step 2: Remove** the code, config, tests and Ansible entries listed above.
- [ ] **Step 3: Verify the migration** against the dev DB from `.claude/settings.local.json`: `alembic -c sources/alembic.ini heads` prints exactly one head; `DISCORD_TOKEN=dummy alembic -c sources/alembic.ini upgrade head`, then `downgrade -1`, then `upgrade head` all exit 0.
- [ ] **Step 4: Run** `python -m pytest tests/ -q`, `ruff check sources/ tests/`, `ruff format --check sources/`, and both `ansible-playbook … --syntax-check` commands → all clean.
- [ ] **Step 5: Commit** `refactor(twitch-relay): remove the user OAuth flow and drop twitch_auth`.

### Task 10: Docs, final verification, rollout (needs the user)

**Files:** Modify `README.md`, `CLAUDE.md`.

- [ ] **Step 1: Docs.** README config table: add `TWITCH_EVENTSUB_SECRET`, `TWITCH_EVENTSUB_CALLBACK_URL`, `TWITCH_RECONCILE_INTERVAL_MINUTES`; remove `ENCRYPTION_KEY`. CLAUDE.md: project-structure tree (new files, `crypto.py` and `twitch_auth.py` gone), config table, models (remove `TwitchAuth`), command table (remove `/twitch-relay authorize`; `sync` now recreates subscriptions), health/metrics section (new `POST /twitch/eventsub` route, proxied by nginx only on that path), architecture/dependencies notes (`cryptography` row removed).
- [ ] **Step 2: Local end-to-end.** With the Twitch CLI (`twitch event verify-subscription` and `twitch event trigger stream.online -F <local url> -s <secret>`, see the webhook docs) run the bot's health server locally and confirm: challenge answered, trigger accepted (204) and processed.
- [ ] **Step 3: Whole-branch review** (fresh reviewer on the diff), then fix findings.
- [ ] **Step 4: Commit** the docs (`docs: describe the Twitch webhook relay`), ask "Push?", open PR 2 and wait for CI.
- [ ] **Step 5: Rollout (only with the user's explicit approval for each action):** merge → `./deploy.sh`; then verify: logs show the reconcile creating `2 × channels` subscriptions and the logged `max_total_cost`; `get_eventsub_subscriptions` lists them all `enabled` with the production callback; Prometheus gauges match; `/twitch-relay force-check` reports "not currently live" for offline channels; observe a real stream start and end (post, edit with VOD link, session removed); restart the container once and confirm a later delivery is still processed. Afterwards: delete the obsolete Infisical secret `discord-bot-encryption-key`, and optionally revoke the old user token in the Twitch account's Connections settings.

Rollback: redeploy the previous image, `alembic downgrade -1`, run `/twitch-relay authorize` again; delete the webhook subscriptions at Twitch first (otherwise Twitch keeps calling the endpoint until it revokes them).

---

## Self-review notes

- Spec coverage: transport and handler (Tasks 4–5), subscription lifecycle and `sync` recreate (6–7), live reconcile and missed-start note (8), removal and migration (9), ansible/nginx/config/credentials (1–3), docs and rollout (10). Metrics: counter in 5, gauges in 6, old gauge removed in 7.
- Deviation from the spec, marked as the implementer's call: no `Ratelimit-Reset` backoff by default (Task 6, Step 1) — failures are retried by the next reconcile tick.
- Added beyond the spec: a per-channel `asyncio.Lock` in `_post_live_notification` (Task 8), because the reconcile and a webhook notification can otherwise both announce the same stream.
