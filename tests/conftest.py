"""Pytest configuration — env stubs and integration test fixtures."""

import os
import subprocess
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

# Pydantic-settings validates these at import time; provide stubs so cog modules can be imported.
os.environ.setdefault('DB_LOGIN', 'test')
os.environ.setdefault('DB_PASSWORD', 'test')
os.environ.setdefault('DB_HOST', 'localhost')
os.environ.setdefault('DB_DATABASE', 'test')
os.environ.setdefault('DISCORD_TOKEN', 'test')


def _run_migrations(url: str) -> None:
    """Run alembic upgrade head against the given database URL.

    Args:
        url: psycopg3 async URL for the target database.

    Raises:
        RuntimeError: If alembic exits with a non-zero return code.
    """
    result = subprocess.run(
        ['python', '-m', 'alembic', '-c', 'sources/alembic.ini', 'upgrade', 'head'],
        env={
            **os.environ,
            'TEST_ALEMBIC_URL': url,
            'DISCORD_TOKEN': os.environ.get('DISCORD_TOKEN', 'test'),
        },
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'alembic upgrade head failed:\n{result.stdout}\n{result.stderr}'
        )


@pytest.fixture(scope='session')
def pg_async_url() -> str:
    """Provide a psycopg3 async URL for the test database and run migrations.

    In CI (``TEST_DB_URL`` set): uses the pre-provisioned postgres service
    container provided by GitHub Actions — no Docker pull required.

    Locally (``TEST_DB_URL`` not set): spins up a throwaway postgres container
    via testcontainers.

    Session-scoped: migrations run once for the entire test session.
    Alembic is invoked via subprocess so migration files are exercised directly.

    Yields:
        psycopg3 async URL for the test database.
    """
    ci_url = os.environ.get('TEST_DB_URL')
    if ci_url:
        _run_migrations(ci_url)
        yield ci_url
        return

    with PostgresContainer('postgres:17', driver=None) as pg:
        host = pg.get_container_host_ip()
        port = str(pg.get_exposed_port(5432))
        url = f'postgresql+psycopg://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}'
        _run_migrations(url)
        yield url


@pytest.fixture
async def db_session(pg_async_url: str) -> AsyncGenerator[AsyncSession, None]:
    """Provide an async SQLAlchemy session bound to the test PostgreSQL container.

    Function-scoped: a fresh session is created for each test. The session is
    closed (but not rolled back) after the test — test data persists in the
    container for the session lifetime. Tests use unique ID ranges to avoid
    cross-test interference.

    Args:
        pg_async_url: The psycopg3 async URL from the session-scoped container fixture.

    Yields:
        An open AsyncSession ready for use in tests.
    """
    engine = create_async_engine(pg_async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()
