# Discord Meow Bot — Project Guide

## Overview

A Discord bot written in Python 3.12 with `discord.py` v2.7.1. Features: URL domain fixing (Reddit, Twitter, TikTok → privacy-friendly mirrors), voice channel auto-status, user timezone management, timestamp generation, and guild info commands. Deployed via Ansible + Docker on a remote server.

## Project Structure

```
sources/
├── scripts/
│   ├── main.py           # Entry point — MeowBot class and startup
│   └── create_db.py      # DB initialization (run before alembic)
├── lib/
│   ├── cogs/             # Discord Cogs — one file per feature group
│   │   ├── admin.py      # ?sync-tree command
│   │   ├── guild.py      # /info, /list-members, guild event listeners
│   │   ├── messages.py   # URL domain fixing listener
│   │   ├── user.py       # /set-timezone, /get-timestamp
│   │   └── voice.py      # Voice channel auto-status
│   ├── commands/         # Reusable command helpers
│   │   ├── get_timestamp.py
│   │   └── utils.py
│   ├── db/
│   │   ├── __init__.py   # Async engine + session factory
│   │   ├── models.py     # SQLAlchemy ORM: Guild, User
│   │   ├── utils.py      # DB helper utilities
│   │   ├── crud/base.py  # Generic CRUD (get, create, update, delete, upsert)
│   │   ├── operations/   # Domain-specific: users.py, guilds.py
│   │   └── alembic/      # Migrations
│   ├── on_message/
│   │   └── domains_fixer.py  # URL rewriting logic
│   └── utils.py          # Logger singleton (get_logger)
├── config.py             # Pydantic settings — DBConfig + Config
└── alembic.ini
sources/prod.txt
ansible/                  # Deployment — see Deployment section
deploy.sh                 # Runs ansible-playbook for zelgray.work inventory
```

## Architecture Principles

- **Cogs** are the top-level feature boundaries. Each cog registers its own Discord commands and event listeners. Add new features by creating a new cog and loading it in `setup_hook()` in `main.py`.
- **Async everywhere.** Use `asyncpg` for DB, `discord.py` 2.x async API, `asyncio`-scoped sessions. Never block the event loop.
- **CRUD layer is generic.** `crud/base.py` provides typed generic operations. Domain-specific logic (e.g., upsert a user) lives in `db/operations/`.
- **Config is environment-driven.** All secrets come from environment variables (see `config.py`). No secrets in code or YAML.

## Configuration

Pydantic Settings (`sources/config.py`). All values can be set via environment variables:

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token |
| `DB_LOGIN` | PostgreSQL user |
| `DB_PASSWORD` | PostgreSQL password (URL-encoded automatically) |
| `DB_HOST` | PostgreSQL host |
| `DB_DATABASE` | Database name |
| `DB_PORT` | PostgreSQL port (default: 5432) |

Both sync (`postgresql+psycopg2://`) and async (`postgresql+asyncpg://`) URLs are constructed from these variables.

## Logging

Logger helper: `sources/lib/utils.py` → `get_logger(name: str) -> Logger`

```python
from sources.lib.utils import get_logger

logger = get_logger(__name__)
logger.info('Processing guild: %s', guild.name)
```

Rules:
- Always use `get_logger` — do not instantiate `logging.Logger` directly.
- Always use %-formatting in log calls — no f-strings in logger arguments.

## Database

### Models

`sources/lib/db/models.py`:
- `Guild(id: BigInteger PK, name: Text)`
- `User(id: BigInteger PK, name: Text, timezone: Text)`
- `DomainFixer(source_domain: Text PK, replacement_domain: Text, override_subdomain: Text nullable)` — URL replacement rules; `override_subdomain=NULL` keeps the original subdomain

### Migrations

```bash
# Create a new migration after changing models.py
alembic -c sources/alembic.ini revision --autogenerate -m 'description'

# Apply migrations
alembic -c sources/alembic.ini upgrade head
```

Run `create_db.py` once before first alembic run (creates DB if not exists).

### CRUD Usage Pattern

```python
from sources.lib.db.crud.base import get_db_entity, update_db_entity_or_create
from sources.lib.db.models import User

# Get
user = await get_db_entity(session, User, User.id == discord_id)

# Upsert
await update_db_entity_or_create(session, User, {'id': discord_id}, {'name': name})
```

Domain-specific wrappers live in `sources/lib/db/operations/`.

## Adding a New Feature

1. Create `sources/lib/cogs/my_feature.py` with a `Cog` class.
2. Register slash commands with `@app_commands.command()`, event listeners with `@commands.Cog.listener()`.
3. Load the cog in `sources/scripts/main.py` → `setup_hook()`.
4. If new DB tables are needed: add model to `models.py`, add operations in `db/operations/`, generate migration.

## Running Locally

```bash
# Install dependencies
pip install -r sources/prod.txt

# Set environment variables (DISCORD_TOKEN, DB_*)
export DISCORD_TOKEN=...

# Initialize DB (first time only)
python sources/scripts/create_db.py

# Apply migrations
alembic -c sources/alembic.ini upgrade head

# Start bot
python sources/scripts/main.py
```

## Deployment

Ansible-based, targets `zelgray.work` inventory. Secrets are Ansible Vault-encrypted in `ansible/inventories/zelgray.work/group_vars/all.yml`.

```bash
# Deploy
./deploy.sh
# Equivalent to:
ansible-playbook -i inventories/zelgray.work -vv playbooks/deploy.yml
```

Deployment does:
1. Builds Docker image (AlmaLinux 10 + Python 3.12)
2. Starts container with startup sequence: `create_db.py` → `alembic upgrade head` → `main.py`
3. Log driver: `journald`; restart policy: `always`
4. Sources mounted read-only at `/opt/docker/volumes/meow-bot/sources`

## No Tests

There is no test suite. When adding logic that can be tested without Discord, consider adding pytest tests in a `tests/` directory.

## Key Dependencies

| Package | Version | Purpose |
|---|---|---|
| `discord.py` | v2.7.1 (git) | Discord API |
| `SQLAlchemy[asyncio]` | 2.0.43 | ORM |
| `asyncpg` | 0.30.0 | Async PostgreSQL driver |
| `alembic` | 1.16.5 | DB migrations |
| `pydantic-settings` | 2.10.1 | Config from env vars |
| `tldextract` | 5.3.0 | URL domain extraction |
| `dateparser` | git/master | Natural language date parsing |
