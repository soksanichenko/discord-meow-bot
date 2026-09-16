"""Symmetric encryption helper for secrets stored at rest (e.g. Twitch tokens)."""

from cryptography.fernet import Fernet

from sources.config import config


class EncryptionKeyMissingError(RuntimeError):
    """Raised when ENCRYPTION_KEY is required but not configured."""


def _fernet() -> Fernet:
    key = config.encryption_key.get_secret_value()
    if not key:
        raise EncryptionKeyMissingError(
            'ENCRYPTION_KEY is not configured. Generate one with '
            '`python3 -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"` and set it in the environment.'
        )
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    """Encrypt a string for storage.

    Args:
        plaintext: The value to encrypt.

    Returns:
        A URL-safe encrypted token, safe to store as text.
    """
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by encrypt().

    Args:
        token: The encrypted token.

    Returns:
        The original plaintext value.
    """
    return _fernet().decrypt(token.encode()).decode()
