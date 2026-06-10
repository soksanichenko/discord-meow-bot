"""Config module"""

from urllib.parse import quote_plus

from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


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
    health_port: int = 8080
    birthday_images_dir: str = '/tmp/meow-bot-images'
    youtube_api_key: str = ''
    spotify_api_client_id: str = ''
    spotify_api_client_secret: str = ''
    rsshub_url: str = 'https://rsshub.app'
    telegram_relay_poll_interval_minutes: int = 5
    youtube_relay_poll_interval_minutes: int = 5
    twitch_client_id: str = ''
    twitch_client_secret: str = ''
    kvizgame_port: int = 8082
    kvizgame_packs_dir: str = '/tmp/kvizgame-packs'
    kvizgame_sessions_dir: str = '/tmp/kvizgame-sessions'
    kvizgame_frontend_dir: str = ''
    discord_client_id: str = ''
    discord_client_secret: str = ''
    sync_db_url: str = (
        f'postgresql+psycopg://{__db.login}:{__db.password}@'
        f'{__db.host}:{__db.port}/{__db.database}'
    )
    async_db_url: str = (
        f'postgresql+psycopg://{__db.login}:{__db.password}@'
        f'{__db.host}:{__db.port}/{__db.database}'
    )


config = Config()
