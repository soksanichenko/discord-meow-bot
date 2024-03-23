"""Config module"""

from urllib.parse import quote_plus

from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)
from pydantic import field_validator


class DBConfig(BaseSettings):
    """Database settings"""

    password: str
    login: str
    host: str
    port: int = 5432
    database: str

    model_config = SettingsConfigDict(env_prefix='db_')

    @field_validator('password')
    @classmethod
    def validate_password(cls, password: str) -> str:
        """Validate and encode password"""
        return quote_plus(password)


class Config(BaseSettings):
    """General settings"""

    __db: DBConfig = DBConfig()

    discord_token: str
    sync_db_url: str = (
        f'postgresql+psycopg2://{__db.login}:{__db.password}@'
        f'{__db.host}:{__db.port}/{__db.database}'
    )
    async_db_url: str = (
        f'postgresql+asyncpg://{__db.login}:{__db.password}@'
        f'{__db.host}:{__db.port}/{__db.database}'
    )


config = Config()
