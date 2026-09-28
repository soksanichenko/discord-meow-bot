# Exact-Once Message Statistics — Design

Date: 2026-09-28
Status: Approved in chat, pending written-spec review

## Goal

Every non-bot guild message is counted in `message_stats` exactly once, including:

- messages sent while the bot was down (deploy, crash, full gateway reconnect);
- messages sent while a history import is running;
- a crash at any point (mid-import, mid-catch-up, with unflushed live counts).

Admins can wipe a guild's statistics and rebuild them from history.

## Problems with the current implementation

- `/stats import` reads `channel.history()` with no upper bound, so it re-counts
  messages that the live `on_message` counter already counted.
- A channel marked `is_completed` is never read again, so downtime gaps are lost.
- There is no way to reset; re-running `/stats import` is a no-op.
- Counts and the import checkpoint are written in separate transactions, so a
  crash between them double-counts up to one checkpoint batch.
- Auto-resume runs in `cog_load`, before the gateway connects, so it never finds
  any guild and never resumes.

## Data model

### `stats_import_progress` (changed)

One row per `(guild_id, channel_id)`:

| Column | Type | Meaning |
|---|---|---|
| `guild_id` | BigInteger PK, FK guilds CASCADE | |
| `channel_id` | BigInteger PK | |
| `oldest_id` | BigInteger NOT NULL | Lower bound of the counted range (was `last_message_id`) |
| `newest_id` | BigInteger NOT NULL | Upper bound of the counted range (new) |
| `is_completed` | Boolean NOT NULL | The backward history import reached the start of the channel (or `since`) |

**Invariant:** `message_stats` contains exactly the non-bot messages of the channel
with `oldest_id <= id <= newest_id`, each counted once. Every write that moves a
bound is in the same transaction as the count increments it covers.

Bounds may be synthetic snowflakes built from a timestamp
(`discord.utils.time_snowflake`), not real message IDs. An empty range is
`oldest_id = newest_id = B`.

### `stats_import_jobs` (new)

| Column | Type | Meaning |
|---|---|---|
| `guild_id` | BigInteger PK, FK guilds CASCADE | Guild with a requested, unfinished history import |
| `since` | DateTime(tz) NULL | Lower time bound for the import; NULL means the whole history |

Created by `/stats import`, `/stats reset` and the migration. Deleted when the
backward import finishes all channels.

### `message_stats`

Unchanged.

### Migration

1. Insert a `stats_import_jobs` row (`since = NULL`) for every guild that has rows in
   `message_stats` or `stats_import_progress`.
2. Delete all rows from `message_stats` and `stats_import_progress`.
3. Rename `last_message_id` to `oldest_id` and make it NOT NULL, then add
   `newest_id BigInteger NOT NULL`. Both tables are empty at this point, so no
   backfill is needed.

The downgrade reverses the schema change. It cannot restore the wiped counts.

## Runtime state (in memory, per process)

- `boundary[guild_id]` — snowflake from which the live counter owns messages.
- `floor[channel_id]` — live counting ignores messages with `id <= floor`. Defaults
  to the guild's boundary.
- `caught_up: set[channel_id]` — channels whose persisted `newest_id` has reached
  the boundary.
- Live buffer: `(guild_id, channel_id) -> {counts: {user_id: n}, max_id}`.
- Task references for catch-up and import tasks, per guild.

## Data flow

### Boundary

`boundary[guild]` is set to `time_snowflake(now)`:

- on every `on_ready`, which also fires after a full gateway reconnect;
- on `/stats reset` for that guild;
- on `on_guild_join`.

### `on_ready`

Steps 1–2 run synchronously, before the first `await`, so no live message can
land in the buffer between taking the snapshot and moving the floors.

1. Cancel running catch-up tasks, set a new boundary `B` for every guild, set every
   known channel's `floor = B`, and clear the caught-up set.
2. Take the live buffer out. Keep the entries of channels that were caught up
   before step 1; drop the others, because the new catch-up covers them.
3. Flush the kept entries. After this, each caught-up channel's `newest_id`
   covers everything the live counter saw.
4. For each guild, for every existing progress row and every readable
   `guild.text_channels` channel:
   - no row: insert `oldest_id = newest_id = B`, mark caught up;
   - `newest_id < B`: set `floor = B`, start catch-up over `(newest_id, B]`;
     the channel is not caught up until catch-up completes.
5. Resume backward imports for every guild in `stats_import_jobs` that has no
   running import task.

### Live counter (`on_message`)

- Skip bot authors and DMs.
- Skip messages with `id <= floor[channel]`; catch-up covers them.
- Otherwise add to the buffer for `(guild, channel)` and update `max_id`.
- A channel with no floor (created after `on_ready`, a thread, a voice text chat)
  gets `floor = boundary[guild]` and is treated as caught up.

### Flush (every 30 s, and on unload)

For each buffered channel that is caught up, one transaction:

- increment the counts;
- upsert the progress row with `newest_id = GREATEST(newest_id, max_id)`,
  inserting `oldest_id = newest_id = boundary` if the row is missing.

Buffers of channels that are not caught up stay in memory. If the process dies,
they are lost, but `newest_id` did not move, so the next catch-up counts those
messages.

On `OperationalError`, the batch goes back into the buffer. Any other exception
drops the batch with a logged error. Nothing is re-raised, so the `tasks.loop`
never stops.

### Catch-up (forward)

`channel.history(after=newest_id, before=B, oldest_first=True)`:

- every 500 messages: one transaction with the increments and
  `newest_id = last message id`;
- at the end: one transaction with the remaining increments and `newest_id = B`,
  then mark the channel caught up;
- channel not found, or `Forbidden`: set `newest_id = B` and log a warning. That gap
  cannot be read and is lost.

### Backward import

For each readable `guild.text_channels` channel whose row is not `is_completed`
(creating a row at the guild boundary if missing):

`channel.history(before=oldest_id, after=since, oldest_first=False)`:

- every 500 messages: one transaction with the increments and
  `oldest_id = last message id`;
- at the end of the channel: remaining increments and `is_completed = True`.

When all channels are done, delete the guild's `stats_import_jobs` row.

Catch-up and the import write different columns of the same row and can run in
parallel.

`/stats import` on a guild whose channels are already completed only picks up
channels that are not completed yet (for example, new channels). A full re-read
is done with `/stats reset`.

### Reset (`/stats reset`)

1. Cancel the guild's catch-up and import tasks and drop its live buffer.
2. In one transaction: delete the guild's `message_stats` and
   `stats_import_progress` rows and upsert a `stats_import_jobs` row with
   `since = NULL`.
3. Set a new boundary for the guild. All of its channels become caught up with
   `floor` equal to that boundary.
4. Start the backward import.

## Scope

- The live counter keeps counting every guild channel type, as today.
- The history import and catch-up cover `guild.text_channels` plus any channel
  that already has a progress row and can be resolved with
  `guild.get_channel_or_thread`.

## Code structure

- `sources/lib/stats_tracker.py` — `StatsTracker` class: buffer, boundaries,
  floors, caught-up set, catch-up and import tasks, flush.
- `sources/lib/db/operations/stats.py` — atomic
  `apply_counts(guild_id, channel_id, counts, *, newest_id=None, oldest_id=None,
  is_completed=None)`, job CRUD, reset, empty-range row creation. The functions
  that are no longer used (`save_channel_progress`,
  `get_guilds_with_incomplete_import`, standalone `increment_message_counts`)
  are removed.
- `sources/lib/cogs/stats.py` — slash commands and listeners only, delegating to
  the tracker.
- `sources/lib/db/models.py` and a new Alembic migration.

## Commands

| Command | Change |
|---|---|
| `/stats leaderboard` | Unchanged |
| `/stats import [since]` | Upserts the job (replacing `since`) and starts the import; replies if an import is already running |
| `/stats import-status` | Running yes/no, channels imported of total, channels currently catching up |
| `/stats reset` | New, admin only (`manage_guild`, runtime check). Ephemeral confirm button, 60 s timeout. Then runs the reset flow above |

## Error handling

- A catch-up or import step that fails on a DB error or a Discord 5xx is logged and
  the channel is retried after 60 s. Checkpoints are atomic, so a retry never
  double-counts.
- While a channel is not caught up, its live counts stay in memory.
- `cog_unload` cancels all tasks and flushes the buffer.

## Testing

- **Exact-once scenarios** with fake channels whose `history()` replays a list of
  fake messages, and an in-memory fake of the operations layer that enforces the
  same atomicity. After each scenario the counts must equal a reference count of
  the messages:
  1. live counting overlapping catch-up;
  2. crash mid catch-up (task cancelled after a checkpoint), then restart;
  3. crash while a not-caught-up buffer is in memory, then restart;
  3a. full gateway reconnect (second `on_ready`) during catch-up, with buffered
      counts in both caught-up and not-caught-up channels;
  4. backward import running in parallel with live counting;
  5. import resumed after restart, honouring `since`;
  6. reset, then new messages.
- **DB integration** (testcontainers Postgres, `tests/db`): `apply_counts`
  atomicity and cursor moves, and the migration's wipe-and-enqueue behaviour.
- **Verification against the real database** (per `CLAUDE.md`): the migration
  wipes statistics while the old code is still running in production, so this
  step needs explicit confirmation before it runs.
