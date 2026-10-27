"""Config module"""

from urllib.parse import quote_plus

from pydantic import SecretStr, field_validator
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

    discord_token: SecretStr
    health_port: int = 8080
    birthday_images_dir: str = '/tmp/meow-bot-images'
    youtube_api_key: str = ''
    spotify_api_client_id: str = ''
    spotify_api_client_secret: SecretStr = SecretStr('')
    rsshub_url: str = 'https://rsshub.app'
    telegram_relay_poll_interval_minutes: int = 5
    youtube_relay_poll_interval_minutes: int = 5
    twitch_client_id: str = ''
    twitch_client_secret: SecretStr = SecretStr('')
    encryption_key: SecretStr = SecretStr('')
    twitch_eventsub_secret: SecretStr = SecretStr('')
    twitch_eventsub_callback_url: str = ''
    twitch_reconcile_interval_minutes: int = 5
    sync_db_url: str = (
        f'postgresql+psycopg://{__db.login}:{__db.password}@'
        f'{__db.host}:{__db.port}/{__db.database}'
    )
    async_db_url: str = (
        f'postgresql+psycopg://{__db.login}:{__db.password}@'
        f'{__db.host}:{__db.port}/{__db.database}'
    )

    @field_validator('twitch_eventsub_secret')
    @classmethod
    def validate_eventsub_secret(cls, secret: SecretStr) -> SecretStr:
        """Check the EventSub secret is empty (relay disabled) or 10-100 ASCII chars."""
        value = secret.get_secret_value()
        if value and not (10 <= len(value) <= 100 and value.isascii()):
            raise ValueError('must be 10-100 ASCII characters')
        return secret

    @field_validator('twitch_eventsub_callback_url')
    @classmethod
    def validate_eventsub_callback_url(cls, url: str) -> str:
        """Check the EventSub callback URL is empty (relay disabled) or HTTPS."""
        if url and not url.startswith('https://'):
            raise ValueError('must start with https://')
        return url


config = Config()
