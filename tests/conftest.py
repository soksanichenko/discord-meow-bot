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


@pytest.fixture(scope='session')
def pg_async_url() -> str:
    """Start a PostgreSQL container, run alembic migrations, and yield the async URL.

    Session-scoped: the container starts once for the entire test session.
    Alembic migrations are run via subprocess so that migration files themselves
    are exercised, not just the ORM model definitions.

    Yields:
        psycopg3 async URL for the test database.
    """
    with PostgresContainer('postgres:17', driver=None) as pg:
        host = pg.get_container_host_ip()
        port = str(pg.get_exposed_port(5432))
        url = f'postgresql+psycopg://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}'

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
